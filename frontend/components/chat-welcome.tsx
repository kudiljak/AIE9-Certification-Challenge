import { Sparkles, CalendarDays, Palette, UserRound } from "lucide-react"

const suggestions = [
  {
    icon: CalendarDays,
    text: "Book an appointment",
    description: "Schedule your next visit",
  },
  {
    icon: Palette,
    text: "Explore our services",
    description: "Cuts, color, treatments & more",
  },
  {
    icon: UserRound,
    text: "Meet our stylists",
    description: "Find your perfect match",
  },
  {
    icon: Sparkles,
    text: "Bridal & special occasions",
    description: "Make your day unforgettable",
  },
]

interface ChatWelcomeProps {
  onSuggestionClick: (text: string) => void
}

export function ChatWelcome({ onSuggestionClick }: ChatWelcomeProps) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-6 text-center">
      <div className="w-16 h-16 rounded-full bg-secondary flex items-center justify-center mb-6">
        <Sparkles size={24} className="text-accent" />
      </div>
      <h2 className="font-serif text-3xl md:text-4xl font-light text-foreground mb-3 text-balance">
        Welcome to Maison Lumiere
      </h2>
      <p className="text-sm text-muted-foreground max-w-sm leading-relaxed mb-10">
        Your personal booking concierge. How may I assist you today?
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-md">
        {suggestions.map((suggestion) => (
          <button
            key={suggestion.text}
            onClick={() => onSuggestionClick(suggestion.text)}
            className="group flex items-start gap-3 p-4 border border-border bg-card text-left hover:border-accent/40 hover:bg-secondary/60 transition-all duration-300 rounded-lg"
          >
            <div className="w-8 h-8 rounded-full bg-secondary flex-shrink-0 flex items-center justify-center group-hover:bg-accent/10 transition-colors duration-300">
              <suggestion.icon
                size={14}
                className="text-muted-foreground group-hover:text-accent transition-colors duration-300"
              />
            </div>
            <div>
              <span className="text-sm font-medium text-foreground block">
                {suggestion.text}
              </span>
              <span className="text-xs text-muted-foreground mt-0.5 block">
                {suggestion.description}
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
