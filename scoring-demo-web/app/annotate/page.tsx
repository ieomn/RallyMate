import AnnotationWorkspace from "./AnnotationWorkspace";

export const metadata = {
  title: "标注工作台 · RallyMate",
  description: "读取已完成的视频，核对原文标准并保存人工标注。",
};

export default function AnnotationPage() {
  return <AnnotationWorkspace />;
}
