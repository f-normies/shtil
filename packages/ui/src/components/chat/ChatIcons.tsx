import type { ReactNode } from "react";

export function ChatIcon({ name }: { name: "mic" | "send" | "play" | "pause" | "stop" }) {
  const paths: Record<typeof name, ReactNode> = {
    mic: (
      <>
        <rect x="9" y="2" width="6" height="12" rx="3" />
        <path d="M5 10a7 7 0 0 0 14 0M12 17v5m-4 0h8" />
      </>
    ),
    send: <path d="m3 11 18-8-8 18-2.5-7.5L3 11Zm7.5 2.5L21 3" />,
    play: <path d="m7 4 13 8-13 8V4Z" fill="currentColor" stroke="none" />,
    pause: <path d="M7 4h4v16H7zM15 4h4v16h-4z" fill="currentColor" stroke="none" />,
    stop: <rect x="5" y="5" width="14" height="14" rx="2" fill="currentColor" stroke="none" />,
  };

  return (
    <svg
      aria-hidden="true"
      className="ui-chat__icon"
      fill="none"
      focusable="false"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
      viewBox="0 0 24 24"
    >
      {paths[name]}
    </svg>
  );
}
