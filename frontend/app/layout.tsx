import type { Metadata } from "next";
import { Inter, Share_Tech_Mono } from "next/font/google";
import "./globals.css";

import Sidebar from "@/components/layout/Sidebar";
import TopBar from "@/components/layout/TopBar";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  weight: ["300", "400", "500", "600"],
});
const techMono = Share_Tech_Mono({
  subsets: ["latin"],
  variable: "--font-tech-mono",
  weight: "400",
});

export const metadata: Metadata = {
  title: "Cisco AI Config Agent",
  description: "AI-powered network configuration for Cisco C1111",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${techMono.variable} bg-page text-ink`}>
        <div className="flex min-h-screen">
          <Sidebar />
          <div className="flex flex-1 flex-col">
            <TopBar />
            <main className="flex-1 overflow-auto p-5">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
