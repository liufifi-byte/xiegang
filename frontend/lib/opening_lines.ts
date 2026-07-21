import { MemberId } from "./types";

/**
 * 5 议员进入议会时的"开场白"——群聊仪式感。
 *
 * 进入议会大厅后，5 个议员按顺序（每 1.2s 一个）发"开始调研"短句。
 * 这是前端的"引导节奏"，不等后端 LLM 出结果。
 */

export const MEMBER_OPENING_LINES: Record<MemberId, string> = {
  mathematician: "先把变量和不确定性拆开。",
  economist: "我先核对机会成本和激励。",
  psychologist: "我先看看真正影响你的心理变量。",
  historian: "我去找相似案例和它们的边界。",
  artist: "我先看看这个问题有没有第三种问法。",
  politician: "我去梳理谁握有筹码和谈判空间。",
  suan:  "开始调研。先把数字摆出来。",
  chen:  "开始调研。最坏的情况我先看。",
  ji:    "开始调研。窗口期我要看一眼。",
  nian:  "开始调研。10 年后回看的视角我准备一下。",
  fan:   "开始调研。这次的共识有什么盲点，我去挑。",
};

/**
 * 后端调研完成后，把"开始调研"开场白替换成的"调研完成"前缀。
 * 用户在群聊会感知到"议员刚才在思考，现在出活了"。
 */
export const MEMBER_RESEARCH_DONE_PREFIX: Record<MemberId, string> = {
  mathematician: "变量和敏感性已经梳理完：",
  economist: "机会成本和激励已经核对完：",
  psychologist: "关键心理冲突已经识别：",
  historian: "相似案例与差异已经整理：",
  artist: "新的问题框架和选项已经形成：",
  politician: "相关者和谈判空间已经梳理：",
  suan:  "调研完成。数字如下：",
  chen:  "调研完成。下行风险我看完了：",
  ji:    "调研完成。窗口期和机会成本：",
  nian:  "调研完成。10 年视角的发现：",
  fan:   "调研完成。共识里的盲点：",
};

/**
 * 议员按"议会先后顺序"出场（"反对"放在最后，作为质疑者出场更有戏）。
 */
export const MEMBER_ENTRY_ORDER: MemberId[] = [
  "mathematician",
  "economist",
  "psychologist",
  "artist",
  "politician",
];
