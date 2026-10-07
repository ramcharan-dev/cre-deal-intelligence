import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { SiteNav } from "@/components/site-nav";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "CRE AI Deal Intelligence",
  description: "AI-assisted commercial real estate deal analysis",
};

const themeScript = `(function(){var root=document.documentElement;try{var stored=localStorage.getItem("theme");if(stored==="dark"||(stored!=="light"&&matchMedia("(prefers-color-scheme: dark)").matches))root.classList.add("dark")}catch(e){}document.addEventListener("click",function(event){var node=event.target;if(node&&node.nodeType!==1)node=node.parentElement;if(!node||!node.closest("[data-theme-toggle]"))return;var next=root.classList.contains("dark")?"light":"dark";root.classList.toggle("dark",next==="dark");try{localStorage.setItem("theme",next)}catch(e){}})})();`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: themeScript,
          }}
        />
      </head>
      <body className="min-h-full flex flex-col">
        <SiteNav />
        {children}
      </body>
    </html>
  );
}
