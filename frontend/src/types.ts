export type Channel = "face" | "voice" | "stt" | "assistant" | "system";

export type ComputeLocation = "local" | "remote";

export interface ServiceCompute {
  location: ComputeLocation;
  host: string;
  port: number;
}

export interface LoadInfo {
  cpu: number;
  mem_gb: number;
  mem_total_gb?: number;
  mem_percent?: number;
  warn: boolean;
}

export interface TranscriptTurn {
  speaker: "you" | "client" | string;
  text: string;
  ts?: number;
}

export interface StatusMessage {
  channel: "system";
  type: "status";
  service: string;
  level: "info" | "warn" | "error" | string;
  text: string;
}

export interface InboundMessage {
  channel: Channel | string;
  type?: string;
  action?: string;
  ok?: boolean;
  error?: string;
  text?: string;
  delta?: string;
  done?: boolean;
  jpeg?: string;
  speaker?: string;
  cpu?: number;
  mem_gb?: number;
  mem_total_gb?: number;
  mem_percent?: number;
  warn?: boolean;
  consent?: boolean;
  mem_warn_gb?: number;
  granted?: boolean;
  enabled?: boolean;
  models?: string[];
  model_id?: string;
  style?: string;
  [key: string]: unknown;
}

export interface OutboundMessage {
  channel: Channel;
  action: string;
  [key: string]: unknown;
}
