import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "なりすまし検知",
  description: "AI合成音声・ディープフェイクによるなりすましを検知します",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}

