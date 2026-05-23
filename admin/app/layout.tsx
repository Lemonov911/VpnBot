import type { Metadata } from "next";
import { Toaster } from "sonner";
import "./globals.css";

export const metadata: Metadata = {
  title: "MAX Admin",
  robots: "noindex, nofollow",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className="bg-[#0f0f0f] text-white antialiased min-h-screen">
        {children}
        {/* sonner — единый канал для уведомлений (foundation D). Фактический
            переход alert/confirm → toast делают треки A/B/C. */}
        <Toaster richColors position="bottom-right" theme="dark" />
      </body>
    </html>
  );
}
