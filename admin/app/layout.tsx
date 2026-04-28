import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MAX Admin",
  robots: "noindex, nofollow",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className="bg-[#0f0f0f] text-white antialiased min-h-screen">{children}</body>
    </html>
  );
}
