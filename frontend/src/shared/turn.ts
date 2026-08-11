import type { ChatTurnRequest, InitiativeTrigger } from "../types";

export type TurnSend = (
  text?: string,
  mode?: "primary" | "regenerate",
  targetRound?: number,
  initiative?: boolean,
  initiativeTrigger?: InitiativeTrigger,
  initiativeSequence?: number,
  initiativeSequenceLimit?: number,
  replayRequest?: ChatTurnRequest,
) => Promise<void>;
