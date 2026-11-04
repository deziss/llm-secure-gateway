import json
from typing import Optional


def generate_client_snippets(
    base_url: str,
    api_key: str,
    model: str = "default",
) -> dict[str, str]:
    """Generate ready-to-run client snippets for various tools, CLIs, and SDKs.

    Args:
        base_url: The root gateway URL (e.g., http://localhost:8000).
        api_key: The API key for authentication.
        model: Target model name (default: "default").

    Returns:
        dict with formatted snippets for:
        - claude_code: Bash export environment variables for Claude Code CLI
        - cursor: Cursor setup instructions and endpoint configuration
        - continue_dev: Config JSON block for ~/.continue/config.json
        - python_openai: Python code using the official OpenAI SDK
        - python_anthropic: Python code using the official Anthropic SDK
        - curl_openai: Ready-to-run cURL command for OpenAI /v1/chat/completions
        - curl_anthropic: Ready-to-run cURL command for Anthropic /v1/messages
    """
    clean_base_url = (base_url or "").rstrip("/")
    if not clean_base_url:
        clean_base_url = "http://localhost:8000"

    effective_key = api_key if api_key else "YOUR_API_KEY"
    effective_model = model if model else "default"

    claude_code = (
        f'export ANTHROPIC_BASE_URL="{clean_base_url}"\n'
        f'export ANTHROPIC_API_KEY="{effective_key}"'
    )

    cursor = (
        f"# Cursor Setup Instructions\n"
        f"Base URL: {clean_base_url}/v1\n"
        f"Model: {effective_model}\n"
        f"API Key: {effective_key}\n\n"
        f"Configuration Steps in Cursor:\n"
        f"1. Open Settings -> Models (or Cursor Settings -> Features -> OpenAI API Key)\n"
        f"2. Toggle 'Override OpenAI Base URL' to ON\n"
        f"3. Set Base URL to: {clean_base_url}/v1\n"
        f"4. Enter API Key: {effective_key}\n"
        f"5. Under Model Names, add: {effective_model}\n"
        f"6. Select '{effective_model}' as your active model."
    )

    continue_dev_dict = {
        "models": [
            {
                "title": f"LLM Gateway ({effective_model})",
                "provider": "openai",
                "model": effective_model,
                "apiBase": f"{clean_base_url}/v1",
                "apiKey": effective_key,
            }
        ]
    }
    continue_dev = json.dumps(continue_dev_dict, indent=2)

    python_openai = (
        "from openai import OpenAI\n\n"
        f'client = OpenAI(base_url="{clean_base_url}/v1", api_key="{effective_key}")\n\n'
        "response = client.chat.completions.create(\n"
        f'    model="{effective_model}",\n'
        "    messages=[\n"
        '        {"role": "user", "content": "Hello!"}\n'
        "    ],\n"
        ")\n"
        "print(response.choices[0].message.content)"
    )

    python_anthropic = (
        "import anthropic\n\n"
        f'client = anthropic.Anthropic(base_url="{clean_base_url}", api_key="{effective_key}")\n\n'
        "message = client.messages.create(\n"
        f'    model="{effective_model}",\n'
        "    max_tokens=1024,\n"
        "    messages=[\n"
        '        {"role": "user", "content": "Hello!"}\n'
        "    ],\n"
        ")\n"
        "print(message.content[0].text)"
    )

    curl_openai = (
        f'curl -X POST "{clean_base_url}/v1/chat/completions" \\\n'
        f'  -H "Content-Type: application/json" \\\n'
        f'  -H "Authorization: Bearer {effective_key}" \\\n'
        f'  -d \'{{\n'
        f'    "model": "{effective_model}",\n'
        f'    "messages": [\n'
        f'      {{"role": "user", "content": "Hello!"}}\n'
        f'    ]\n'
        f'  }}\''
    )

    curl_anthropic = (
        f'curl -X POST "{clean_base_url}/v1/messages" \\\n'
        f'  -H "Content-Type: application/json" \\\n'
        f'  -H "x-api-key: {effective_key}" \\\n'
        f'  -H "anthropic-version: 2023-06-01" \\\n'
        f'  -d \'{{\n'
        f'    "model": "{effective_model}",\n'
        f'    "max_tokens": 1024,\n'
        f'    "messages": [\n'
        f'      {{"role": "user", "content": "Hello!"}}\n'
        f'    ]\n'
        f'  }}\''
    )

    return {
        "claude_code": claude_code,
        "cursor": cursor,
        "continue_dev": continue_dev,
        "python_openai": python_openai,
        "python_anthropic": python_anthropic,
        "curl_openai": curl_openai,
        "curl_anthropic": curl_anthropic,
    }
