from typing import Tuple, Dict

from app.models import ModelSchema
from provider_dependency.chat_completion import *
from .utils import *

logger = logging.getLogger(__name__)


def _build_llama_api_message(message: ChatCompletionMessage):
    if message.role == ChatCompletionRole.system:
        return {"role": message.role.name, "content": message.content}

    if message.role == ChatCompletionRole.user:
        if isinstance(message.content, str):
            return {"role": message.role.name, "content": message.content}
        elif isinstance(message.content, List):
            return {
                "role": message.role.name,
                "content": [c.model_dump() for c in message.content],
            }

    if message.role == ChatCompletionRole.function:
        message: ChatCompletionFunctionMessage
        return {"role": "tool", "content": message.content, "tool_call_id": message.id}

    if is_assistant_text_message(message):
        return {"role": message.role.name, "content": message.content}

    if is_assistant_function_calls_message(message):
        message: ChatCompletionAssistantMessage
        function_calls = []

        for f in message.function_calls:
            arguments = f.arguments
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments)
            function_calls.append(
                {
                    "id": f.id,
                    "type": "function",
                    "function": {"name": f.name, "arguments": arguments},
                }
            )

        return {
            "role": ChatCompletionRole.assistant.name,
            "tool_calls": function_calls,
            "content": None,
        }


def _build_llama_api_chat_completion_payload(
    messages: List[ChatCompletionMessage],
    stream: bool,
    provider_model_id: str,
    configs: ChatCompletionModelConfiguration,
    function_call: Optional[str],
    functions: Optional[List[ChatCompletionFunction]],
):
    # Convert ChatCompletionMessages to the required format
    formatted_messages = [_build_llama_api_message(msg) for msg in messages]
    logger.debug("formatted_messages: %s", formatted_messages)
    payload = {
        "messages": formatted_messages,
        "model": provider_model_id,
        "stream": stream,
    }
    config_dict = configs.model_dump()
    for key, value in config_dict.items():
        if value is not None:
            payload[key] = value

    if function_call:
        payload["function_call"] = {"name": function_call}
    if functions:
        payload["functions"] = [f.model_dump() for f in functions]
    logger.debug(f"_build_llama_api_chat_completion_payload: {payload}")
    return payload


class LlamaApiChatCompletionModel(BaseChatCompletionModel):
    def __init__(self):
        super().__init__()

    # ------------------- prepare request data -------------------

    async def prepare_request(
        self,
        stream: bool,
        provider_model_id: str,
        messages: List[ChatCompletionMessage],
        credentials: ProviderCredentials,
        configs: ChatCompletionModelConfiguration,
        function_call: Optional[str] = None,
        functions: Optional[List[ChatCompletionFunction]] = None,
        model_schema: ModelSchema = None,
    ) -> Tuple[str, Dict, Dict]:
        # todo accept user's api_url
        api_url = "https://api.llama-api.com/chat/completions"
        headers = build_llama_api_header(credentials)
        payload = _build_llama_api_chat_completion_payload(
            messages, stream, provider_model_id, configs, function_call, functions
        )
        return api_url, headers, payload

    # ------------------- handle non-stream chat completion response -------------------

    def extract_core_data(self, response_data: Dict, **kwargs) -> Optional[Dict]:
        if not response_data.get("choices"):
            return None
        return response_data["choices"][0]

    def extract_text_content(self, data: Dict, **kwargs) -> Optional[str]:
        message_data = data.get("message") if data else None
        if message_data and message_data.get("content"):
            return message_data.get("content")
        return None

    def extract_function_calls(self, data: Dict, **kwargs) -> Optional[List[ChatCompletionFunctionCall]]:
        message_data = data.get("message") if data else None
        if message_data.get("function_call"):
            function_call_dict = message_data.get("function_call")
            func_call = build_function_call(
                name=function_call_dict["name"],
                arguments_dict=function_call_dict["arguments"],
            )

            return [func_call]

        return None

    def extract_finish_reason(self, data: Dict, **kwargs) -> Optional[ChatCompletionFinishReason]:
        finish_reason = data.get("finish_reason", "unknown")
        if finish_reason == "tool_calls" or finish_reason == "function_calls" or finish_reason == "function_call":
            finish_reason = ChatCompletionFinishReason.function_calls
        if finish_reason == "max_token":
            finish_reason = ChatCompletionFinishReason.length
        return ChatCompletionFinishReason.__members__.get(finish_reason, ChatCompletionFinishReason.unknown)

    # ------------------- handle stream chat completion response -------------------

    def stream_check_error(self, sse_data: Dict, **kwargs):
        if sse_data.get("error"):
            raise_provider_api_error(sse_data["error"])

    def stream_extract_chunk_data(self, sse_data: Dict, **kwargs) -> Optional[Dict]:
        if not sse_data.get("choices"):
            return None
        return sse_data["choices"][0]

    def stream_extract_chunk(
        self, index: int, chunk_data: Dict, text_content: str, **kwargs
    ) -> Tuple[int, Optional[ChatCompletionChunk]]:
        content = chunk_data.get("delta", {}).get("content") if chunk_data else None
        if content:
            return index + 1, ChatCompletionChunk(
                created_timestamp=get_current_timestamp_int(),
                index=index,
                delta=content,
            )
        return index, None

    def stream_extract_finish_reason(self, chunk_data: Dict, **kwargs) -> Optional[ChatCompletionFinishReason]:
        if chunk_data.get("finish_reason"):
            reason = chunk_data["finish_reason"]
            if reason == "tool_calls" or reason == "function_calls" or reason == "function_call":
                reason = ChatCompletionFinishReason.function_calls
            if reason == "max_token":
                reason = ChatCompletionFinishReason.length
            return ChatCompletionFinishReason.__members__.get(reason, ChatCompletionFinishReason.unknown)
        return None

    def stream_handle_function_calls(
        self, chunk_data: Dict, function_calls_content: ChatCompletionFunctionCallsContent, **kwargs
    ) -> Optional[ChatCompletionFunctionCallsContent]:
        delta = chunk_data.get("delta", {})
        if delta and delta.get("tool_calls"):
            tool_call = delta["tool_calls"][0]
            toll_call_index = tool_call["index"]
            tool_call_function = tool_call["function"]

            if toll_call_index == function_calls_content.index:
                # append to the current function call argument string
                function_calls_content.arguments_strs[function_calls_content.index] += tool_call_function["arguments"]

            elif toll_call_index > function_calls_content.index:
                # trigger another function call
                function_calls_content.arguments_strs.append(tool_call_function["arguments"] or "")
                function_calls_content.names.append(tool_call_function["name"])
                function_calls_content.index = toll_call_index
            return function_calls_content

        return None
