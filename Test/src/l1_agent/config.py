"""L1 Agent 配置 — 从环境变量读取，无硬编码。"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class L1Config:
    # LLM 配置（OpenAI 兼容接口，默认火山豆包方舟平台）
    llm_api_key: str
    llm_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    llm_model: str = "doubao-1.5-pro-32k"

    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"
    task_queue: str = "control-plane"

    template_repo_root: str = "templates"

    # MCP 标注平台配置（见 Test/mcp-server-api-reference.html）
    mcp_transport: str = "mock"  # mock / http
    mcp_server_url: str = ""  # http://172.16.11.11:8079/api/mcp/
    mcp_auth_url: str = ""  # http://172.16.11.11:8079/api/auth/login
    mcp_username: str = ""
    mcp_password: str = ""
    mcp_jwt_token: str = ""  # 直接传 token（可选，优先于用户名密码）

    max_design_rounds: int = 5
    max_schema_retries: int = 3

    @classmethod
    def from_env(cls) -> "L1Config":
        return cls(
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            llm_model=os.environ.get("LLM_MODEL", "doubao-1.5-pro-32k"),
            temporal_host=os.environ.get("TEMPORAL_HOST", "localhost:7233"),
            temporal_namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
            task_queue=os.environ.get("TASK_QUEUE", "control-plane"),
            template_repo_root=os.environ.get("TEMPLATE_REPO_ROOT", "templates"),
            mcp_transport=os.environ.get("MCP_TRANSPORT", "mock"),
            mcp_server_url=os.environ.get("MCP_SERVER_URL", ""),
            mcp_auth_url=os.environ.get("MCP_AUTH_URL", ""),
            mcp_username=os.environ.get("MCP_USERNAME", ""),
            mcp_password=os.environ.get("MCP_PASSWORD", ""),
            mcp_jwt_token=os.environ.get("MCP_JWT_TOKEN", ""),
            max_design_rounds=int(os.environ.get("MAX_DESIGN_ROUNDS", "5")),
            max_schema_retries=int(os.environ.get("MAX_SCHEMA_RETRIES", "3")),
        )
