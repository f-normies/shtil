export type ChatRole = "doctor" | "patient";

export interface ChatPeerStatus {
  label: string;
  online?: boolean;
}

export type ChatTranscript =
  | { status: "queued" }
  | { status: "done"; text: string }
  | { status: "empty" }
  | { status: "error" };

export type ChatContent =
  | { type: "text"; text: string }
  | { type: "voice"; src: string; transcript?: ChatTranscript };

export interface ChatMessage {
  id: string;
  seq: number;
  sender: "self" | "peer";
  createdAt: string;
  content: ChatContent;
}

export interface ChatLocalItem {
  id: string;
  createdAt: string;
  content: ChatContent;
  status: "queued" | "failed";
}

export interface ChatViewProps {
  role: ChatRole;
  peerName: string;
  peerStatus?: ChatPeerStatus;
  messages: ChatMessage[];
  localItems?: ChatLocalItem[];
  draft: string;
  onDraftChange: (draft: string) => void;
  onSendText: (text: string) => void | Promise<void>;
  onReadThrough?: (seq: number) => void;
  readThroughSeq?: number;
  peerReadThroughSeq?: number;
  hasOlderMessages?: boolean;
  olderLoading?: boolean;
  onLoadOlder?: () => void | Promise<void>;
  loadState?: "loading" | "ready" | "error";
  loadError?: string;
  onRetryLoad?: () => void;
  isPeerTyping?: boolean;
  onTypingChange?: (active: boolean) => void;
  onRetryLocal?: (id: string) => void;
  onDeleteLocal?: (id: string) => void;
  onSendVoice?: (audio: Blob) => void | Promise<void>;
  inputLabel?: string;
  disabled?: boolean;
}
