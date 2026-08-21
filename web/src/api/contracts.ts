import type { components } from "./schema";

type Schemas = components["schemas"];

export type LoginChannel = Schemas["CapabilitiesResponse"]["web_login_channels"][number];
export type LegacyLoginChannel = Schemas["ChallengeCreateRequest"]["target_channel"];
export type LoginChallenge = Schemas["ChallengeCreateResponse"];
export type ChallengeStatus = Schemas["ChallengeStatusResponse"];
export type SessionInfo = Schemas["SessionResponse"];
export type Capabilities = Schemas["CapabilitiesResponse"];
export type EmailChallengeInput = Schemas["EmailChallengeRequest"];
export type EmailVerifyInput = Schemas["EmailVerifyRequest"];
export type AcceptedResponse = Schemas["AcceptedResponse"];
export type ApiErrorResponse = Schemas["AuthErrorResponse"];

export type LinkTokenInput = Schemas["LinkTokenInput"];
export type LinkTokenResponse = Schemas["LinkTokenResponse"];
export type ConsumeLinkTokenInput = Schemas["ConsumeLinkTokenInput"];
export type LinkedResponse = Schemas["LinkedResponse"];

export type BrowserDevice = Schemas["BrowserDeviceResponse"];
export type BrowserDeviceList = Schemas["BrowserDeviceListResponse"];
export type PairingApproval = Schemas["PairingApprovalResponse"];

export type BatchSubmitInput = Schemas["BatchSaveRequest"];
export type BatchSubmitItem = Schemas["BatchItemResponse"];
export type BatchSubmitResponse = Schemas["BatchSaveResponse"];

export type Chapter = Schemas["ChapterResponse"];
export type LibraryItem = Schemas["LibraryItemResponse"];
export type LibraryLifecycle = LibraryItem["lifecycle"];
export type LibraryPageResponse = Schemas["LibraryPageResponse"];
export type LibraryItemSummary = Pick<
  LibraryItem,
  | "public_id"
  | "lifecycle"
  | "title"
  | "author"
  | "cover_url"
  | "duration_sec"
  | "saved_at"
  | "why_saved"
  | "url"
  | "error_code"
  | "available_actions"
>;

export type TranscriptBlock = Schemas["TranscriptBlockResponse"];
export type TranscriptPage = Schemas["TranscriptPageResponse"];

export type ConversationCitation = Schemas["ConversationCitationResponse"];
export type ConversationResponse = Schemas["ConversationResponse"];
export type ConversationStreamEvent = Schemas["ConversationStreamEvent"];
export type ConversationHistoryItem = Schemas["ConversationHistoryItemResponse"];
export type ConversationHistoryPage = Schemas["ConversationHistoryPageResponse"];
export type ConversationTurn = Schemas["ConversationTurnResponse"];
export type ConversationTurns = Schemas["ConversationTurnsResponse"];
