"use client"

import { useState, useCallback, useRef } from "react"

export interface ChatMessage {
  id: string
  role: "user" | "assistant"
  content: string
}

const API_URL = process.env.NEXT_PUBLIC_CHAT_API_URL || process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000"

function generateId() {
  return Math.random().toString(36).substring(2, 15)
}

export function useSalonChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [status, setStatus] = useState<"ready" | "streaming">("ready")
  const threadIdRef = useRef<string>(generateId())
  const abortRef = useRef<AbortController | null>(null)

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || status === "streaming") return

    const userMessage: ChatMessage = {
      id: generateId(),
      role: "user",
      content: text.trim(),
    }

    const assistantMessage: ChatMessage = {
      id: generateId(),
      role: "assistant",
      content: "",
    }

    setMessages((prev) => [...prev, userMessage, assistantMessage])
    setStatus("streaming")

    abortRef.current = new AbortController()

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text.trim(),
          thread_id: threadIdRef.current,
        }),
        signal: abortRef.current.signal,
      })

      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`)
      }

      const data = (await response.json()) as { message?: string; error?: string }
      const content = typeof data.message === "string" ? data.message : data.error || ""

      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantMessage.id ? { ...msg, content } : msg
        )
      )
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return
      }
      // On error, update the assistant message with an error notice
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === assistantMessage.id && !msg.content
            ? { ...msg, content: "I apologize, but I'm unable to respond right now. Please try again." }
            : msg
        )
      )
    } finally {
      setStatus("ready")
      abortRef.current = null
    }
  }, [status])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    setStatus("ready")
  }, [])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    setMessages([])
    setStatus("ready")
    threadIdRef.current = generateId()
  }, [])

  return { messages, sendMessage, status, stop, reset }
}
