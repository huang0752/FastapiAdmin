import { request } from "@utils";

const API_PATH = "/ai/chat";

/** 会话分页列表查询（列表接口） */
export interface ChatSessionListQuery extends PageQuery {
  title?: string;
  created_at?: string[];
  updated_at?: string[];
}

export const AiChatAPI = {
  getSessionList(query: ChatSessionListQuery) {
    return request<ApiResponse<PageResult<ChatSession>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  createSession(body: { title: string }) {
    return request<ApiResponse<ChatSession>>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateSession(id: string, body: { title: string }) {
    return request<ApiResponse<ChatSession>>({
      url: `${API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteSession(body: string[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },

  chat(body: { message: string; session_id?: string | null }) {
    return request<ApiResponse<AiChatResponse>>({
      url: `${API_PATH}/ai-chat`,
      method: "post",
      data: body,
    });
  },

  getSessionDetail(sessionId: string) {
    return request<ApiResponse<ChatSessionDetail>>({
      url: `${API_PATH}/detail/${sessionId}`,
      method: "get",
    });
  },

  // ============ AI 模型配置 ============ //
  getModelConfig() {
    return request<ApiResponse<AiModelConfigList>>({
      url: `${API_PATH}/model`,
      method: "get",
    });
  },

  createModelConfig(body: AiModelConfigInput) {
    return request<ApiResponse<AiModelConfigItem>>({
      url: `${API_PATH}/model`,
      method: "post",
      data: body,
    });
  },

  updateModelConfig(id: string, body: AiModelConfigInput) {
    return request<ApiResponse<AiModelConfigItem>>({
      url: `${API_PATH}/model/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteModelConfig(id: string) {
    return request<ApiResponse<null>>({
      url: `${API_PATH}/model/${id}`,
      method: "delete",
    });
  },

  activateModelConfig(id: string) {
    return request<ApiResponse<null>>({
      url: `${API_PATH}/model/${id || "__default__"}/activate`,
      method: "post",
    });
  },

  getFeatureBindings() {
    return request<ApiResponse<AiFeatureBinding[]>>({
      url: `${API_PATH}/feature`,
      method: "get",
    });
  },

  updateFeatureBinding(featureCode: string, body: AiFeatureBindingInput) {
    return request<ApiResponse<AiFeatureBinding>>({
      url: `${API_PATH}/feature/${featureCode}`,
      method: "put",
      data: body,
    });
  },
};

export interface AiModelConfigInput {
  name: string;
  base_url: string;
  api_key?: string | null;
  model_id: string;
  temperature: number;
  provider_type: "openai_compatible";
  timeout_seconds: number;
  max_tokens: number;
  allow_business_data: boolean;
}

export interface AiModelConfigItem {
  id: string;
  name: string;
  base_url: string;
  model_id: string;
  temperature: number;
  provider_type: "openai_compatible";
  timeout_seconds: number;
  max_tokens: number;
  allow_business_data: boolean;
  created_time: string | null;
  has_api_key: boolean;
  api_key_masked: string | null;
}

export interface AiModelConfigList {
  items: AiModelConfigItem[];
  active_id: string | null;
}

export interface AiFeatureBindingInput {
  model_config_id: string | null;
  fallback_config_id: string | null;
  prompt_version: string;
  timeout_seconds: number;
  allow_business_data: boolean;
  enabled: boolean;
}

export interface AiFeatureBinding extends AiFeatureBindingInput {
  feature_code: string;
  feature_name: string;
  description: string;
  prompt_key: string;
}

export default AiChatAPI;

export interface ChatSessionMessage {
  id: string;
  role: string;
  content: string;
  created_at: number | null;
}

export interface ChatSession {
  session_id: string;
  agent_id: string | null;
  team_id: string | null;
  team_name: string | null;
  workflow_id: string | null;
  user_id: string | null;
  session_data: Record<string, any> | null;
  agent_data: Record<string, any> | null;
  team_data: Record<string, any> | null;
  workflow_data: Record<string, any> | null;
  metadata: Record<string, any> | null;
  runs: Array<Record<string, any>> | null;
  summary: Record<string, any> | null;
  created_at: number | null;
  updated_at: number | null;

  id: string;
  title: string | null;
  created_time: string | null;
  updated_time: string | null;
  message_count: number;
  messages: ChatSessionMessage[];
}

export interface SessionGroup {
  id: string;
  title: string;
  sessions: ChatSession[];
}

export interface UserInfo {
  id: number;
  name: string;
  username: string;
  avatar: string;
  email: string;
}

export interface AiChatResponse {
  response: string;
  session_id: string;
  function_calls: Array<{
    name: string;
    arguments: Record<string, any>;
  }> | null;
  action: Record<string, any> | null;
}

export interface ChatSessionDetail {
  session_id: string;
  agent_id: string | null;
  team_id: string | null;
  team_name: string | null;
  workflow_id: string | null;
  user_id: string | null;
  session_data: Record<string, any> | null;
  agent_data: Record<string, any> | null;
  team_data: Record<string, any> | null;
  workflow_data: Record<string, any> | null;
  metadata: Record<string, any> | null;
  runs: Array<Record<string, any>> | null;
  summary: Record<string, any> | null;
  created_at: number | null;
  updated_at: number | null;

  id: string;
  title: string | null;
  created_time: string | null;
  updated_time: string | null;
  message_count: number;
  messages: ChatSessionMessage[];
}
