"use client"

import { ChatHeader } from "@/components/chat-header"
import { ChatWelcome } from "@/components/chat-welcome"
import { ChatMessages } from "@/components/chat-messages"
import { ChatInput } from "@/components/chat-input"
import { useSalonChat } from "@/hooks/use-salon-chat"

export default function Home() {
  const { messages, sendMessage, status, reset } = useSalonChat()

  const isLoading = status === "streaming"

  return (
    <main className="flex flex-col h-dvh bg-background">
      <ChatHeader onReset={reset} hasMessages={messages.length > 0} />
      <div className="flex-1 flex flex-col min-h-0">
        {messages.length === 0 ? (
          <ChatWelcome onSuggestionClick={sendMessage} />
        ) : (
          <ChatMessages messages={messages} isLoading={isLoading} />
        )}
      </div>
      <ChatInput onSend={sendMessage} isLoading={isLoading} />
    </main>
  )
}
