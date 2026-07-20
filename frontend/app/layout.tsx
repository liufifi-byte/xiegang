import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "邪杠会议室",
  description: "把一个决定，想清楚五遍",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
