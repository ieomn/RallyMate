import type { Metadata } from "next";
import ReadOnlySample from "./ReadOnlySample";

export const metadata: Metadata = { title: "只读交付样例 · RallyMate", description: "先了解报告怎样连接片段、观察和下一次训练。虚构格式样例，无对应原视频。" };

export default function SamplePage() { return <ReadOnlySample />; }
