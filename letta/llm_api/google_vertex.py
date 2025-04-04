import uuid
from typing import List, Optional

from letta.constants import NON_USER_MSG_PREFIX
from letta.helpers.datetime_helpers import get_utc_time
from letta.helpers.json_helpers import json_dumps
from letta.local_llm.json_parser import clean_json_string_extra_backslash
from letta.local_llm.utils import count_tokens
from letta.schemas.openai.chat_completion_request import Tool
from letta.schemas.openai.chat_completion_response import ChatCompletionResponse, Choice, FunctionCall, Message, ToolCall, UsageStatistics
from letta.tracing import log_event
from letta.utils import get_tool_call_id


def add_dummy_model_messages(messages: List[dict]) -> List[dict]:
    """Google AI API requires all function call returns are immediately followed by a 'model' role message.

    In Letta, the 'model' will often call a function (e.g. send_message) that itself yields to the user,
    so there is no natural follow-up 'model' role message.

    To satisfy the Google AI API restrictions, we can add a dummy 'yield' message
    with role == 'model' that is placed in-betweeen and function output
    (role == 'tool') and user message (role == 'user').
    """
    dummy_yield_message = {"role": "model", "parts": [{"text": f"{NON_USER_MSG_PREFIX}Function call returned, waiting for user response."}]}
    messages_with_padding = []
    for i, message in enumerate(messages):
        messages_with_padding.append(message)
        # Check if the current message role is 'tool' and the next message role is 'user'
        if message["role"] in ["tool", "function"] and (i + 1 < len(messages) and messages[i + 1]["role"] == "user"):
            messages_with_padding.append(dummy_yield_message)

    return messages_with_padding


# TODO use pydantic model as input
def to_google_ai(openai_message_dict: dict) -> dict:

    # TODO supports "parts" as part of multimodal support
    assert not isinstance(openai_message_dict["content"], list), "Multi-part content is message not yet supported"
    if openai_message_dict["role"] == "user":
        google_ai_message_dict = {
            "role": "user",
            "parts": [{"text": openai_message_dict["content"]}],
        }
    elif openai_message_dict["role"] == "assistant":
        google_ai_message_dict = {
            "role": "model",  # NOTE: diff
            "parts": [{"text": openai_message_dict["content"]}],
        }
    elif openai_message_dict["role"] == "tool":
        google_ai_message_dict = {
            "role": "function",  # NOTE: diff
            "parts": [{"text": openai_message_dict["content"]}],
        }
    else:
        raise ValueError(f"Unsupported conversion (OpenAI -> Google AI) from role {openai_message_dict['role']}")


# TODO convert return type to pydantic
def convert_tools_to_google_ai_format(tools: List[Tool], inner_thoughts_in_kwargs: Optional[bool] = True) -> List[dict]:
    """
    OpenAI style:
      "tools": [{
        "type": "function",
        "function": {
            "name": "find_movies",
            "description": "find ....",
            "parameters": {
              "type": "object",
              "properties": {
                 PARAM: {
                   "type": PARAM_TYPE,  # eg "string"
                   "description": PARAM_DESCRIPTION,
                 },
                 ...
              },
              "required": List[str],
            }
        }
      }
      ]

    Google AI style:
      "tools": [{
        "functionDeclarations": [{
          "name": "find_movies",
          "description": "find movie titles currently playing in theaters based on any description, genre, title words, etc.",
          "parameters": {
            "type": "OBJECT",
            "properties": {
              "location": {
                "type": "STRING",
                "description": "The city and state, e.g. San Francisco, CA or a zip code e.g. 95616"
              },
              "description": {
                "type": "STRING",
                "description": "Any kind of description including category or genre, title words, attributes, etc."
              }
            },
            "required": ["description"]
          }
        }, {
          "name": "find_theaters",
          ...
    """
    function_list = [
        dict(
            name=t.function.name,
            description=t.function.description,
            parameters=t.function.parameters,  # TODO need to unpack
        )
        for t in tools
    ]

    # Correct casing + add inner thoughts if needed
    for func in function_list:
        func["parameters"]["type"] = "OBJECT"
        for param_name, param_fields in func["parameters"]["properties"].items():
            param_fields["type"] = param_fields["type"].upper()
        # Add inner thoughts
        if inner_thoughts_in_kwargs:
            from letta.local_llm.constants import INNER_THOUGHTS_KWARG, INNER_THOUGHTS_KWARG_DESCRIPTION

            func["parameters"]["properties"][INNER_THOUGHTS_KWARG] = {
                "type": "STRING",
                "description": INNER_THOUGHTS_KWARG_DESCRIPTION,
            }
            func["parameters"]["required"].append(INNER_THOUGHTS_KWARG)

    return [{"functionDeclarations": function_list}]


def convert_google_ai_response_to_chatcompletion(
    response,
    model: str,  # Required since not returned
    input_messages: Optional[List[dict]] = None,  # Required if the API doesn't return UsageMetadata
    pull_inner_thoughts_from_args: Optional[bool] = True,
) -> ChatCompletionResponse:
    """Google AI API response format is not the same as ChatCompletion, requires unpacking

    Example:
    {
      "candidates": [
        {
          "content": {
            "parts": [
              {
                "text": " OK. Barbie is showing in two theaters in Mountain View, CA: AMC Mountain View 16 and Regal Edwards 14."
              }
            ]
          }
        }
      ],
      "usageMetadata": {
        "promptTokenCount": 9,
        "candidatesTokenCount": 27,
        "totalTokenCount": 36
      }
    }
    """
    try:
        choices = []
        index = 0
        for candidate in response.candidates:
            content = candidate.content

            role = content.role
            assert role == "model", f"Unknown role in response: {role}"

            parts = content.parts
            # TODO support parts / multimodal
            # TODO support parallel tool calling natively
            # TODO Alternative here is to throw away everything else except for the first part
            for response_message in parts:
                # Convert the actual message style to OpenAI style
                if response_message.function_call:
                    function_call = response_message.function_call
                    function_name = function_call.name
                    function_args = function_call.args
                    assert isinstance(function_args, dict), function_args

                    # NOTE: this also involves stripping the inner monologue out of the function
                    if pull_inner_thoughts_from_args:
                        from letta.local_llm.constants import INNER_THOUGHTS_KWARG

                        assert INNER_THOUGHTS_KWARG in function_args, f"Couldn't find inner thoughts in function args:\n{function_call}"
                        inner_thoughts = function_args.pop(INNER_THOUGHTS_KWARG)
                        assert inner_thoughts is not None, f"Expected non-null inner thoughts function arg:\n{function_call}"
                    else:
                        inner_thoughts = None

                    # Google AI API doesn't generate tool call IDs
                    openai_response_message = Message(
                        role="assistant",  # NOTE: "model" -> "assistant"
                        content=inner_thoughts,
                        tool_calls=[
                            ToolCall(
                                id=get_tool_call_id(),
                                type="function",
                                function=FunctionCall(
                                    name=function_name,
                                    arguments=clean_json_string_extra_backslash(json_dumps(function_args)),
                                ),
                            )
                        ],
                    )

                else:

                    # Inner thoughts are the content by default
                    inner_thoughts = response_message.text

                    # Google AI API doesn't generate tool call IDs
                    openai_response_message = Message(
                        role="assistant",  # NOTE: "model" -> "assistant"
                        content=inner_thoughts,
                    )

                # Google AI API uses different finish reason strings than OpenAI
                # OpenAI: 'stop', 'length', 'function_call', 'content_filter', null
                #   see: https://platform.openai.com/docs/guides/text-generation/chat-completions-api
                # Google AI API: FINISH_REASON_UNSPECIFIED, STOP, MAX_TOKENS, SAFETY, RECITATION, OTHER
                #   see: https://ai.google.dev/api/python/google/ai/generativelanguage/Candidate/FinishReason
                finish_reason = candidate.finish_reason.value
                if finish_reason == "STOP":
                    openai_finish_reason = (
                        "function_call"
                        if openai_response_message.tool_calls is not None and len(openai_response_message.tool_calls) > 0
                        else "stop"
                    )
                elif finish_reason == "MAX_TOKENS":
                    openai_finish_reason = "length"
                elif finish_reason == "SAFETY":
                    openai_finish_reason = "content_filter"
                elif finish_reason == "RECITATION":
                    openai_finish_reason = "content_filter"
                else:
                    raise ValueError(f"Unrecognized finish reason in Google AI response: {finish_reason}")

                choices.append(
                    Choice(
                        finish_reason=openai_finish_reason,
                        index=index,
                        message=openai_response_message,
                    )
                )
                index += 1

        # if len(choices) > 1:
        #     raise UserWarning(f"Unexpected number of candidates in response (expected 1, got {len(choices)})")

        # NOTE: some of the Google AI APIs show UsageMetadata in the response, but it seems to not exist?
        #  "usageMetadata": {
        #     "promptTokenCount": 9,
        #     "candidatesTokenCount": 27,
        #     "totalTokenCount": 36
        #   }
        if response.usage_metadata:
            usage = UsageStatistics(
                prompt_tokens=response.usage_metadata.prompt_token_count,
                completion_tokens=response.usage_metadata.candidates_token_count,
                total_tokens=response.usage_metadata.total_token_count,
            )
        else:
            # Count it ourselves
            assert input_messages is not None, f"Didn't get UsageMetadata from the API response, so input_messages is required"
            prompt_tokens = count_tokens(json_dumps(input_messages))  # NOTE: this is a very rough approximation
            completion_tokens = count_tokens(json_dumps(openai_response_message.model_dump()))  # NOTE: this is also approximate
            total_tokens = prompt_tokens + completion_tokens
            usage = UsageStatistics(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )

        response_id = str(uuid.uuid4())
        return ChatCompletionResponse(
            id=response_id,
            choices=choices,
            model=model,  # NOTE: Google API doesn't pass back model in the response
            created=get_utc_time(),
            usage=usage,
        )
    except KeyError as e:
        raise e


# TODO convert 'data' type to pydantic
def google_vertex_chat_completions_request(
    model: str,
    project_id: str,
    region: str,
    contents: List[dict],
    config: dict,
    add_postfunc_model_messages: bool = True,
    # NOTE: Google AI API doesn't support mixing parts 'text' and 'function',
    # so there's no clean way to put inner thoughts in the same message as a function call
    inner_thoughts_in_kwargs: bool = True,
) -> ChatCompletionResponse:
    """https://ai.google.dev/docs/function_calling

    From https://ai.google.dev/api/rest#service-endpoint:
    "A service endpoint is a base URL that specifies the network address of an API service.
    One service might have multiple service endpoints.
    This service has the following service endpoint and all URIs below are relative to this service endpoint:
    https://xxx.googleapis.com
    """

    from google import genai
    from google.genai.types import FunctionCallingConfig, FunctionCallingConfigMode, ToolConfig

    client = genai.Client(vertexai=True, project=project_id, location=region, http_options={"api_version": "v1"})
    # add dummy model messages to the end of the input
    if add_postfunc_model_messages:
        contents = add_dummy_model_messages(contents)

    tool_config = ToolConfig(
        function_calling_config=FunctionCallingConfig(
            # ANY mode forces the model to predict only function calls
            mode=FunctionCallingConfigMode.ANY,
        )
    )
    config["tool_config"] = tool_config.model_dump()

    # make request to client
    attributes = config if isinstance(config, dict) else {"config": config}
    attributes.update({"contents": contents})
    log_event(name="llm_request_sent", attributes={"contents": contents, "config": config})
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )

    # convert back response
    try:
        return convert_google_ai_response_to_chatcompletion(
            response=response,
            model=model,
            input_messages=contents,
            pull_inner_thoughts_from_args=inner_thoughts_in_kwargs,
        )
    except Exception as conversion_error:
        print(f"Error during response conversion: {conversion_error}")
        raise conversion_error
