import { Scissors, RotateCcw } from "lucide-react"

interface ChatHeaderProps {
  onReset: () => void
  hasMessages: boolean
}

export function ChatHeader({ onReset, hasMessages }: ChatHeaderProps) {
  return (
    <header className="flex items-center justify-between px-6 py-4 border-b border-border bg-card/80 backdrop-blur-md">
      <div className="flex items-center gap-3">
        <div className="w-9 h-9 rounded-full bg-primary flex items-center justify-center">
          <Scissors size={16} className="text-primary-foreground" />
        </div>
        <div>
          <h1 className="font-serif text-lg font-medium text-foreground tracking-wide">
            Maison Lumiere
          </h1>
          <p className="text-[11px] text-muted-foreground tracking-widest uppercase">
            Booking Assistant
          </p>
        </div>
      </div>
      <div className="flex items-center gap-4">
        {hasMessages && (
          <button
            onClick={onReset}
            className="flex items-center gap-1.5 text-[11px] text-muted-foreground tracking-wide hover:text-foreground transition-colors duration-200"
            aria-label="Start new conversation"
          >
            <RotateCcw size={12} />
            New Chat
          </button>
        )}
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-accent animate-pulse" />
          <span className="text-[11px] text-muted-foreground tracking-wide">
            Online
          </span>
        </div>
      </div>
    </header>
  )
}
