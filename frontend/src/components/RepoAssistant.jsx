"use client";
import { useState } from 'react';
import { MessageCircle, Send } from 'lucide-react';
import { apiUrl } from '../lib/api';

export default function RepoAssistant({ repoId, token, repoName, focusedNode, llmConfigured }) {
  const [question, setQuestion] = useState('');
  const [messages, setMessages] = useState([]);
  const [sending, setSending] = useState(false);

  const ask = async event => {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || sending) return;

    const userMessage = { role: 'user', text: trimmed };
    setMessages(previous => [...previous, userMessage]);
    setQuestion('');
    setSending(true);

    const focusLabel = focusedNode
      ? `Focused block: ${focusedNode.label}\nFile: ${focusedNode.metadata?.path || 'unknown'}\nLanguage: ${focusedNode.metadata?.language || 'unknown'}\nArchitecture type: ${focusedNode.type || 'unknown'}\nRelationships: ${JSON.stringify(focusedNode.relationships || [])}`
      : '';

    try {
      const response = await fetch(apiUrl(`/v1/repositories/${repoId}/ask`), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          question: trimmed,
          analysis_context: focusLabel,
          focus_file_path: focusedNode?.metadata?.metadata?.path || null,
        }),
      });
      const data = await response.json();
      const evidence = (data.evidence || []).slice(0, 3).map(item =>
        `${item.file_path}${item.symbol_name ? ` :: ${item.symbol_name}` : ''}${item.line_start ? ` (${item.line_start}-${item.line_end})` : ''}`
      );
      setMessages(previous => [...previous, {
        role: 'assistant',
        text: data.answer || "I don't have enough evidence in this repository to answer that.",
        evidence,
        abstained: data.abstained,
      }]);
    } catch {
      setMessages(previous => [...previous, {
        role: 'assistant',
        text: 'The repository assistant could not reach the analysis service.',
        abstained: true,
      }]);
    } finally {
      setSending(false);
    }
  };

  return (
    <aside className="repo-assistant" aria-label="Repository assistant">
      <div className="assistant-repo-label">{repoName}</div>
      <div className="assistant-header">
        <span className="assistant-icon"><MessageCircle size={14} /></span>
        <div><strong>Ask about this repo</strong><span>Evidence-backed answers only</span></div>
      </div>
      {!llmConfigured && <div className="assistant-key-note">AI key not configured. Add <b>OPENAI_API_KEY</b> to the backend <b>.env</b>; this browser never handles the key.</div>}
      {focusedNode && <div className="assistant-focus">Focused on <b>{focusedNode.label}</b></div>}
      <div className="assistant-messages" aria-live="polite">
        {messages.length === 0 && <p className="assistant-empty">Ask how a file, function, class, or route works.</p>}
        {messages.map((message, index) => <div className={`assistant-message ${message.role}`} key={`${message.role}-${index}`}>
          <p>{message.text}</p>
          {message.evidence?.length > 0 && <div className="assistant-evidence">Based on: {message.evidence.join(', ')}</div>}
        </div>)}
        {sending && <div className="assistant-thinking">Reading repository evidence...</div>}
      </div>
      <form className="assistant-form" onSubmit={ask}>
        <input value={question} onChange={event => setQuestion(event.target.value)} placeholder="Ask about this code" aria-label="Ask about this repository" disabled={sending} />
        <button className="button button-primary assistant-send" type="submit" disabled={sending || !question.trim()} title="Ask repository assistant"><Send size={14} /></button>
      </form>
    </aside>
  );
}
