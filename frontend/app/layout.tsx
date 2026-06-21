import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PharmacoSignal Intelligence",
  description: "Modern pharmacovigilance analytics frontend for FDA adverse event signal monitoring.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
