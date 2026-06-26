import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider, themeNoFlashScript } from "@/lib/theme";
import { Analytics } from "@vercel/analytics/next";

export const metadata: Metadata = {
  title: "PharmaSignal — Pharmacovigilance Intelligence",
  description:
    "Enterprise pharmacovigilance analytics for FDA adverse-event signal detection, disproportionality, and emerging-risk monitoring.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500;600&display=swap"
        />
        <script dangerouslySetInnerHTML={{ __html: themeNoFlashScript }} />
      </head>
      <body>
        <ThemeProvider>{children}</ThemeProvider>
        <Analytics />
      </body>
    </html>
  );
}
