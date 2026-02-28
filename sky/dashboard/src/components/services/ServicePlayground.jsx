'use client';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { SendIcon, TrashIcon, Loader2Icon } from 'lucide-react';

/**
 * Chat/completion playground UI for testing a model endpoint served by SkyServe.
 *
 * Sends streaming requests to the OpenAI-compatible /v1/chat/completions
 * endpoint exposed by the service, parses SSE responses, and renders the
 * conversation in a chat-bubble layout.
 *
 * @param {Object} props
 * @param {Object} props.serviceData - Service data with name and endpoint
 */
export function ServicePlayground({ serviceData }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(512);
  const [streamEnabled, setStreamEnabled] = useState(true);
  const messagesEndRef = useRef(null);
  const abortControllerRef = useRef(null);

  const endpoint = serviceData?.endpoint;

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  const clearConversation = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setMessages([]);
    setIsGenerating(false);
  };

  const handleSend = useCallback(async () => {
    if (!input.trim() || isGenerating || !endpoint) return;

    const userMessage = { role: 'user', content: input.trim() };
    const updatedMessages = [...messages, userMessage];
    setMessages(updatedMessages);
    setInput('');
    setIsGenerating(true);

    // Build the request body with the full conversation history
    const chatMessages = updatedMessages.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    const requestBody = {
      messages: chatMessages,
      temperature,
      max_tokens: maxTokens,
      stream: streamEnabled,
    };

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const url = `${endpoint.replace(/\/$/, '')}/v1/chat/completions`;
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text();
        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            content: `Error: ${response.status} - ${errorText}`,
          },
        ]);
        setIsGenerating(false);
        return;
      }

      if (streamEnabled) {
        // Handle streaming SSE response
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let assistantContent = '';

        // Add an empty assistant message that we will incrementally update
        setMessages((prev) => [...prev, { role: 'assistant', content: '' }]);

        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          // Keep the last potentially incomplete line in the buffer
          buffer = lines.pop() || '';

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed.startsWith('data: ')) continue;
            const payload = trimmed.slice(6);
            if (payload === '[DONE]') continue;

            try {
              const parsed = JSON.parse(payload);
              const delta = parsed.choices?.[0]?.delta?.content;
              if (delta) {
                assistantContent += delta;
                const snapshot = assistantContent;
                setMessages((prev) => {
                  const updated = [...prev];
                  updated[updated.length - 1] = {
                    role: 'assistant',
                    content: snapshot,
                  };
                  return updated;
                });
              }
            } catch {
              // Skip malformed JSON chunks
            }
          }
        }
      } else {
        // Handle non-streaming response
        const data = await response.json();
        const content =
          data.choices?.[0]?.message?.content || 'No response received.';
        setMessages((prev) => [...prev, { role: 'assistant', content }]);
      }
    } catch (error) {
      if (error.name !== 'AbortError') {
        const isCors =
          error.message === 'Failed to fetch' ||
          error.message.includes('NetworkError');
        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            content: isCors
              ? `Unable to reach the service endpoint directly from the browser (CORS). ` +
                `Try using curl instead:\n\ncurl ${endpoint.replace(/\/$/, '')}/v1/chat/completions ` +
                `-H "Content-Type: application/json" ` +
                `-d '{"messages": [{"role": "user", "content": "${input.trim().replace(/'/g, "\\'")}"}], ` +
                `"temperature": ${temperature}, "max_tokens": ${maxTokens}}'`
              : `Error: ${error.message}`,
          },
        ]);
      }
    } finally {
      setIsGenerating(false);
      abortControllerRef.current = null;
    }
  }, [input, isGenerating, endpoint, messages, temperature, maxTokens, streamEnabled]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (!endpoint) {
    return (
      <div className="mt-6">
        <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
          <div className="p-6">
            <h3 className="text-lg font-semibold mb-2">Playground</h3>
            <p className="text-sm text-gray-500">
              Service endpoint not available. The service must be in READY
              state.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mt-6">
      <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
        <div className="flex items-center justify-between px-4 pt-4">
          <h3 className="text-lg font-semibold">Playground</h3>
          <button
            onClick={clearConversation}
            className="p-1.5 rounded-md text-gray-500 hover:text-gray-700 hover:bg-gray-100 transition-colors"
            aria-label="Clear conversation"
          >
            <TrashIcon className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5">
          {/* Configuration Controls */}
          <div className="mb-4 p-4 bg-gray-50 rounded-md border border-gray-200">
            <div className="flex flex-wrap gap-6 items-center">
              {/* Temperature */}
              <div className="flex items-center gap-2">
                <label className="text-sm font-medium text-gray-700 whitespace-nowrap">
                  Temperature:
                </label>
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.1"
                  value={temperature}
                  onChange={(e) => setTemperature(parseFloat(e.target.value))}
                  className="w-24"
                />
                <span className="text-sm text-gray-600 w-8 text-right">
                  {temperature.toFixed(1)}
                </span>
              </div>

              {/* Max Tokens */}
              <div className="flex items-center gap-2">
                <label className="text-sm font-medium text-gray-700 whitespace-nowrap">
                  Max Tokens:
                </label>
                <input
                  type="number"
                  min="1"
                  max="8192"
                  value={maxTokens}
                  onChange={(e) =>
                    setMaxTokens(
                      Math.max(1, parseInt(e.target.value, 10) || 1)
                    )
                  }
                  className="w-24 px-2 py-1 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>

              {/* Stream Toggle */}
              <div className="flex items-center gap-2">
                <label className="text-sm font-medium text-gray-700 whitespace-nowrap">
                  Stream:
                </label>
                <input
                  type="checkbox"
                  checked={streamEnabled}
                  onChange={(e) => setStreamEnabled(e.target.checked)}
                  className="w-4 h-4 text-blue-600 rounded border-gray-300 focus:ring-blue-500"
                />
              </div>
            </div>
          </div>

          {/* Message List */}
          <div className="mb-4 bg-white rounded-md border border-gray-200 overflow-auto max-h-[500px] min-h-[200px] p-4">
            {messages.length === 0 && (
              <div className="text-sm text-gray-400 text-center mt-16">
                Send a message to start the conversation.
              </div>
            )}
            {messages.map((msg, idx) => (
              <div
                key={idx}
                className={`mb-3 flex ${
                  msg.role === 'user' ? 'justify-end' : 'justify-start'
                }`}
              >
                <div
                  className={`max-w-[80%] px-4 py-2 rounded-lg text-sm whitespace-pre-wrap break-words ${
                    msg.role === 'user'
                      ? 'bg-blue-500 text-white'
                      : 'bg-gray-100 text-gray-800'
                  }`}
                >
                  {msg.content}
                  {msg.role === 'assistant' &&
                    isGenerating &&
                    idx === messages.length - 1 &&
                    !msg.content && (
                      <Loader2Icon className="w-4 h-4 animate-spin inline-block" />
                    )}
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          {/* Input Area */}
          <div className="flex gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type your message..."
              rows={2}
              className="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-md resize-none focus:outline-none focus:ring-1 focus:ring-blue-500"
              disabled={isGenerating}
            />
            <button
              onClick={handleSend}
              disabled={isGenerating || !input.trim()}
              className="px-4 py-2 bg-blue-500 text-white rounded-md hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors self-end"
              aria-label="Send message"
            >
              {isGenerating ? (
                <Loader2Icon className="w-4 h-4 animate-spin" />
              ) : (
                <SendIcon className="w-4 h-4" />
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ServicePlayground;
