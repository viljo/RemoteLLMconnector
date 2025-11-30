# Future Features

## Chat UI Improvements

Features to bring the chat interface closer to ChatGPT-like functionality.

### High Priority

#### Conversation Persistence
- [ ] Sidebar with conversation history
- [ ] Save/load/delete conversations (localStorage or server-side)
- [ ] Auto-generate conversation titles
- [ ] Conversations persist across page refresh

#### Markdown & Code Rendering
- [ ] Markdown rendering (bold, italic, lists, tables, links)
- [ ] Code syntax highlighting (highlight.js or Prism)
- [ ] Code block copy button
- [ ] Language detection for code blocks

### Medium Priority

#### Message Actions
- [ ] Copy message button
- [ ] Regenerate response button
- [ ] Edit previous user messages
- [ ] Thumbs up/down feedback

#### Stop Generation
- [ ] Abort streaming response button
- [ ] AbortController integration

#### System Prompt & Parameters
- [ ] Collapsible system instruction field
- [ ] Temperature slider
- [ ] Max tokens control
- [ ] Settings persistence per conversation

### Lower Priority

#### File Upload
- [ ] Document upload (for context)
- [ ] Image upload (for vision models)
- [ ] Drag-and-drop support

#### UI Polish
- [ ] Dark mode toggle
- [ ] Keyboard shortcuts panel (Ctrl+Enter, etc.)
- [ ] Export conversations (Markdown, JSON)
- [ ] Conversation search
- [ ] Mobile-responsive improvements

---

## Other Future Features

### Broker
- [ ] Rate limiting per user
- [ ] Usage analytics dashboard
- [ ] Model-specific pricing/quotas

### Connector
- [ ] Auto-discovery of local models
- [ ] Multiple LLM backend support (vLLM, TGI)
- [ ] Load balancing across multiple instances

### Security
- [ ] OAuth2/OIDC integration
- [ ] API key rotation
- [ ] Audit logging
