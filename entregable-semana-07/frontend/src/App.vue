<template>
  <div class="app-container">
    <!-- Login Screen Overlay -->
    <div v-if="!isLoggedIn" class="login-overlay">
      <div class="login-card">
        <!-- El tamaño del logo lo maneja `.login-logo img` en style.css (NO con
             estilos inline: un `style=` gana a cualquier @media y dejaria el
             logo fijo en 80px tambien en movil). -->
        <div class="login-logo">
          <img src="./assets/Logos/Mobile Logo/Swingtails Full V3 DEFINITIVO.png" alt="SwingTails Logo" />
          <h1>SwingTails AI</h1>
          <p>Asistente Veterinario Inteligente Local</p>
        </div>

        <!-- Selector Iniciar sesión / Crear cuenta -->
        <div v-if="authMode !== 'forgot'" class="auth-tabs">
          <button
            type="button"
            :class="['auth-tab', { active: authMode === 'login' }]"
            @click="switchAuthMode('login')"
          >
            Iniciar sesión
          </button>
          <button
            type="button"
            :class="['auth-tab', { active: authMode === 'register' }]"
            @click="switchAuthMode('register')"
          >
            Crear cuenta
          </button>
        </div>

        <!-- ============ REGISTRO ============ -->
        <form v-if="authMode === 'register'" @submit.prevent="handleRegister">
          <div class="form-group">
            <label>Nombre completo</label>
            <input
              v-model="regName"
              type="text"
              :class="['form-input', { invalid: touched.name && !nameValid }]"
              placeholder="Ana López"
              @blur="touched.name = true"
            />
            <p v-if="touched.name && !nameValid" class="field-error">
              Escribe tu nombre (mínimo 3 caracteres, sin los símbolos &lt; ni &gt;).
            </p>
          </div>

          <div class="form-group">
            <label>Correo Electrónico</label>
            <input
              v-model="regEmail"
              type="email"
              :class="['form-input', { invalid: touched.email && !emailValid }]"
              placeholder="ejemplo@correo.com"
              @blur="touched.email = true"
            />
            <p v-if="touched.email && !emailValid" class="field-error">
              Escribe un correo válido (ejemplo: nombre@correo.com).
            </p>
          </div>

          <div class="form-group">
            <label>Teléfono <span style="color: var(--text-muted); font-weight: 400;">(opcional)</span></label>
            <input
              v-model="regPhone"
              type="tel"
              inputmode="numeric"
              maxlength="10"
              :class="['form-input', { invalid: touched.phone && !phoneValid }]"
              placeholder="9991234567"
              @blur="touched.phone = true"
            />
            <p v-if="touched.phone && !phoneValid" class="field-error">
              El teléfono debe tener 10 dígitos (o déjalo vacío).
            </p>
          </div>

          <div class="form-group">
            <label>Contraseña</label>
            <div class="password-input-wrapper">
              <input
                v-model="regPassword"
                :type="showRegPassword ? 'text' : 'password'"
                :class="['form-input', { invalid: touched.password && !passwordValid }]"
                placeholder="••••••••"
                @blur="touched.password = true"
              />
              <button
                type="button"
                class="btn-toggle-pwd"
                @click="showRegPassword = !showRegPassword"
                :title="showRegPassword ? 'Ocultar contraseña' : 'Ver contraseña'"
              >
                <EyeOff v-if="showRegPassword" :size="18" />
                <Eye v-else :size="18" />
              </button>
            </div>

            <!-- Requisitos EN VIVO: se marcan en verde conforme se cumplen. Son
                 exactamente los que exige la API de SwingTails (su mensaje de
                 error es generico: "no cumple con los requisitos de seguridad",
                 asi que aqui le decimos al usuario QUE le falta). -->
            <ul v-if="regPassword || touched.password" class="pwd-rules">
              <li v-for="(rule, i) in passwordChecks" :key="i" :class="['pwd-rule', { ok: rule.ok }]">
                <span class="pwd-rule-icon">{{ rule.ok ? '✓' : '○' }}</span>
                {{ rule.label }}
              </li>
            </ul>
          </div>

          <div class="form-group">
            <label>Confirmar contraseña</label>
            <div class="password-input-wrapper">
              <input
                v-model="regPassword2"
                :type="showRegPassword2 ? 'text' : 'password'"
                :class="['form-input', { invalid: touched.password2 && !passwordsMatch }]"
                placeholder="••••••••"
                @blur="touched.password2 = true"
              />
              <button
                type="button"
                class="btn-toggle-pwd"
                @click="showRegPassword2 = !showRegPassword2"
                :title="showRegPassword2 ? 'Ocultar contraseña' : 'Ver contraseña'"
              >
                <EyeOff v-if="showRegPassword2" :size="18" />
                <Eye v-else :size="18" />
              </button>
            </div>
            <p v-if="touched.password2 && !passwordsMatch" class="field-error">
              Las contraseñas no coinciden.
            </p>
          </div>

          <div v-if="registerError" style="color: #e74c3c; font-size: 0.85rem; margin-bottom: 16px; text-align: center; font-weight: 500;">
            {{ registerError }}
          </div>

          <button type="submit" class="btn-primary" :disabled="registerLoading || !registerFormValid">
            <template v-if="registerLoading">
              <svg class="spinner-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                <circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle>
                <path d="M4 12a8 8 0 0 1 8-8V4C5.37 4 0 9.37 0 16h4z" fill="currentColor"></path>
              </svg>
              Creando tu cuenta...
            </template>
            <template v-else>
              Crear cuenta y entrar
            </template>
          </button>

          <p class="auth-hint">
            Al crear tu cuenta podrás usar a Tailo para registrar tus mascotas y agendar citas.
          </p>
        </form>

        <!-- ============ RECUPERAR CONTRASEÑA ============ -->
        <form v-else-if="authMode === 'forgot'" @submit.prevent="handleForgotPassword">
          <div style="margin-bottom: 20px; text-align: center;">
            <h3 style="font-size: 1.25rem; font-weight: 700; color: var(--text-primary); margin-bottom: 6px;">Recuperar contraseña</h3>
            <p style="font-size: 0.85rem; color: var(--text-muted); line-height: 1.4;">
              Ingresa tu correo electrónico registrado y te enviaremos las instrucciones para restablecer tu contraseña.
            </p>
          </div>

          <div class="form-group">
            <label>Correo Electrónico</label>
            <input 
              v-model="forgotEmail" 
              type="email" 
              class="form-input" 
              placeholder="ejemplo@correo.com" 
              required
            />
          </div>

          <div v-if="forgotError" style="color: #e74c3c; font-size: 0.85rem; margin-bottom: 16px; text-align: center; font-weight: 500;">
            {{ forgotError }}
          </div>

          <div v-if="forgotSuccess" style="color: #2ecc71; font-size: 0.85rem; margin-bottom: 16px; text-align: center; font-weight: 500; background: rgba(46, 204, 113, 0.1); padding: 10px 14px; border-radius: 10px; border: 1px solid rgba(46, 204, 113, 0.3);">
            {{ forgotSuccess }}
          </div>

          <button type="submit" class="btn-primary" :disabled="forgotLoading || !forgotEmail">
            <template v-if="forgotLoading">
              <svg class="spinner-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                <circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle>
                <path d="M4 12a8 8 0 0 1 8-8V4C5.37 4 0 9.37 0 16h4z" fill="currentColor"></path>
              </svg>
              Enviando...
            </template>
            <template v-else>
              Enviar instrucciones
            </template>
          </button>

          <div style="margin-top: 16px; text-align: center;">
            <a href="#" @click.prevent="switchAuthMode('login')" style="color: var(--text-muted); font-size: 0.85rem; font-weight: 600;">
              ← Volver a Iniciar sesión
            </a>
          </div>
        </form>

        <!-- ============ LOGIN ============ -->
        <form v-else @submit.prevent="handleLogin">
          <div class="form-group">
            <label>Correo Electrónico (SwingTails)</label>
            <input 
              v-model="email" 
              type="email" 
              class="form-input" 
              placeholder="ejemplo@swingtails.com" 
              required
            />
          </div>

          <div class="form-group">
            <label>Contraseña</label>
            <div class="password-input-wrapper">
              <input 
                v-model="password" 
                :type="showLoginPassword ? 'text' : 'password'" 
                class="form-input" 
                placeholder="••••••••" 
                required
              />
              <button
                type="button"
                class="btn-toggle-pwd"
                @click="showLoginPassword = !showLoginPassword"
                :title="showLoginPassword ? 'Ocultar contraseña' : 'Ver contraseña'"
              >
                <EyeOff v-if="showLoginPassword" :size="18" />
                <Eye v-else :size="18" />
              </button>
            </div>
          </div>

          <div class="forgot-password" style="margin-bottom: 20px; text-align: right;">
            <a href="#" @click.prevent="switchAuthMode('forgot')" style="color: var(--text-muted); font-size: 0.90rem;">¿Olvidaste tu contraseña?</a>
          </div>

          <div v-if="loginError" style="color: #e74c3c; font-size: 0.85rem; margin-bottom: 16px; text-align: center; font-weight: 500;">
            {{ loginError }}
          </div>

          <button type="submit" class="btn-primary" :disabled="loginLoading">
            <template v-if="loginLoading">
              <svg class="spinner-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                <circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle>
                <path d="M4 12a8 8 0 0 1 8-8V4C5.37 4 0 9.37 0 16h4z" fill="currentColor"></path>
              </svg>
              Iniciando Sesión...
            </template>
            <template v-else>
              Conectarse al Agente
            </template>
          </button>
        </form>



      </div>
    </div>

    <!-- Main Workspace Application -->
    <template v-else>
      <!-- Backdrop del drawer en movil -->
      <div v-if="sidebarOpen" class="sidebar-overlay" @click="sidebarOpen = false"></div>

      <!-- Sidebar (Conversations History) -->
      <aside :class="['sidebar', { open: sidebarOpen }]">
        <div class="sidebar-header">
          <div class="logo-container">
            <img src="./assets/Logos/Mobile Logo/Swingtails Full V3 DEFINITIVO.png" alt="SwingTails Icon" style="width: 28px; height: 28px; object-fit: contain; border-radius: 6px;" />
            <h2 style="background: none; -webkit-text-fill-color: var(--custom-brown); color: var(--custom-brown);">SwingTails</h2>
          </div>
          <div style="display: flex; align-items: center; gap: 8px;">
            <button class="btn-new-chat" @click="startNewConversation">
              <Plus :size="16" /> Nueva
            </button>
            <!-- Cerrar drawer (solo visible en movil) -->
            <button class="btn-close-sidebar" @click="sidebarOpen = false" title="Cerrar menú">
              <X :size="18" />
            </button>
          </div>
        </div>

        <!-- Conversations History Scroll -->
        <div class="conversations-list">
          <div 
            v-for="conv in conversations" 
            :key="conv.conversation_id"
            :class="['conversation-item', { active: activeConversationId === conv.conversation_id }]"
            @click="loadConversation(conv.conversation_id)"
          >
            <div class="conversation-info">
              <span class="conversation-title">{{ conv.title || 'Nueva conversación' }}</span>
              <div class="conversation-meta">
                <span>{{ conv.n_messages }} mensajes</span>
                <span>{{ formatDate(conv.updated_at) }}</span>
              </div>
            </div>
            <button class="btn-delete-conv" @click.stop="deleteConversation(conv.conversation_id)">
              <Trash2 :size="14" />
            </button>
          </div>
          <div v-if="conversations.length === 0" style="text-align: center; color: var(--text-muted); font-size: 0.85rem; padding: 20px 0;">
            No hay chats guardados
          </div>
        </div>

        <!-- Sidebar Footer -->
        <div class="sidebar-footer">
          <!-- <button class="sidebar-nav-btn" @click="activeTab = activeTab === 'chat' ? 'audit' : 'chat'">
            <Activity :size="16" />
            <span>{{ activeTab === 'chat' ? 'Ver Bitácora de Auditoría' : 'Volver al Chat' }}</span>
          </button> -->

          <div class="user-profile">
            <div class="user-avatar" :style="currentUser?.imageUrl ? `background-image: url(${currentUser.imageUrl}); background-size: cover; text-indent: -9999px;` : ''">
              {{ userNameLetter }}
            </div>
            <div class="user-info">
              <span class="user-name" style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary); display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 140px;">{{ currentUser?.name || `Usuario ID #${currentUserId}` }}</span>
              <span class="user-id-tag" style="font-size: 0.75rem; color: var(--text-muted); display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 140px;">{{ currentUser?.email || 'Sesión Activa' }}</span>
            </div>
            <button class="btn-delete-conv" style="opacity: 1;" @click="handleLogout" title="Cerrar sesión">
              <LogOut :size="16" />
            </button>
          </div>
        </div>
      </aside>

      <!-- Right Panel Container (Chat Window or Audit Panel) -->
      <main style="flex: 1; height: 100vh; display: flex; flex-direction: column;">
        
        <!-- Tab 1: Chatbot View -->
        <section v-if="activeTab === 'chat'" class="chat-window">
          
          <!-- Chat Header -->
          <header class="chat-header">
            <button class="btn-hamburger" @click="sidebarOpen = true" title="Abrir menú de chats">
              <Menu :size="22" />
            </button>
            <div class="chat-header-info">
              <span class="chat-header-title">{{ chatTitle }}</span>
              <span class="chat-header-subtitle">
                <span class="status-dot"></span>
                <span>Conectado a IA local (Whisper + Llama)</span>
              </span>
            </div>
            <div class="chat-header-actions">
              <button class="btn-header" @click="clearActiveChatMessages" :disabled="messages.length === 0">
                Limpiar pantalla
              </button>
              <button 
                class="btn-header" 
                :class="{ active: transcriptionMethod === 'whisper' }"
                @click="transcriptionMethod = 'whisper'"
                title="Whisper en Backend local"
              >
                Whisper
              </button>
            </div>
          </header>

          <!-- Agent Loading State / Dynamic Banner -->
          <div 
            v-if="isAgentLoading && agentPhase" 
            :class="['agent-status-overlay', agentPhase.phase]"
          >
            <div class="agent-loader"></div>
            <span>{{ agentPhase.detail || 'Procesando...' }}</span>
            <span v-if="agentPhase.tool" style="font-family: monospace; font-size: 0.75rem; background: rgba(0,0,0,0.2); padding: 2px 6px; border-radius: 4px;">
              {{ agentPhase.tool }}()
            </span>
          </div>

          <!-- Messages Container -->
          <div class="messages-container" ref="messagesBox">
            <div v-if="messages.length === 0" class="welcome-screen">
              <img src="./assets/Logos/Mobile Logo/Swingtails Full V3 DEFINITIVO.png" alt="Tailo Logo" style="width: 80px; height: 80px; margin-bottom: 16px; object-fit: contain; border-radius: 16px;" />
              <h3>¡Hola! Soy Tailo</h3>
              <p>Tu asistente veterinario local de SwingTails. Puedo ayudarte a consultar y registrar mascotas, buscar veterinarias y agendar citas en tiempo real.</p>
              
              <div class="welcome-suggestions">
                <div class="suggestion-card" @click="applySuggestion('¿Cuáles son mis mascotas registradas?')">
                  <h4>Mascotas</h4>
                  <p>¿Cuáles son mis mascotas registradas?</p>
                </div>
                <div class="suggestion-card" @click="applySuggestion('Busca clínicas veterinarias disponibles')">
                  <h4>Clínicas</h4>
                  <p>Busca clínicas veterinarias disponibles</p>
                </div>
                <div class="suggestion-card" @click="applySuggestion('Quiero agendar una cita veterinaria')">
                  <h4>Agendar</h4>
                  <p>Quiero agendar una cita veterinaria</p>
                </div>
                <div class="suggestion-card" @click="applySuggestion('¿Qué alimentos tienes en catálogo?')">
                  <h4>Catálogo</h4>
                  <p>¿Qué alimentos tienes en catálogo?</p>
                </div>
              </div>
            </div>

            <!-- Messages Log -->
            <div 
              v-for="(msg, idx) in messages" 
              :key="idx" 
              :class="['message-row', msg.role]"
            >
              <div :class="['message-bubble', { blocked: msg.blocked }]">
                
                <!-- If blocked by Guardrails -->
                <div v-if="msg.blocked" class="guardrail-alert">
                  <ShieldAlert style="color: var(--danger-color); flex-shrink: 0;" :size="20" />
                  <div>
                    <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
                      <span class="guardrail-badge">Guardrail</span>
                      <strong style="color: white; font-size: 0.85rem;">Intento de inyección bloqueado</strong>
                    </div>
                    <p style="margin: 0; font-size: 0.9rem;">{{ msg.content }}</p>
                  </div>
                </div>

                <!-- Standard text / markdown rendering -->
                <div v-else class="message-body">
                  <!-- Asistente pensando (sin contenido aún) -->
                  <div v-if="msg.role === 'assistant' && !msg.content" class="typing-indicator">
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                  <!-- Respuesta del asistente: markdown renderizado (negritas, listas, código) -->
                  <div v-else-if="msg.role === 'assistant'" class="markdown-body" v-html="renderMarkdown(msg.content)"></div>
                  <!-- Mensaje del usuario: texto plano -->
                  <div v-else style="white-space: pre-wrap; word-break: break-word;">{{ msg.content }}</div>
                </div>

                <!-- Observability Metrics (shown on completed bot responses) -->
                <div v-if="msg.role === 'assistant' && msg.metrics && !msg.blocked" class="message-metrics">
                  <div class="metric-item" title="Tiempo hasta el primer token emitido">
                    <Clock :size="12" />
                    <span>TTFT:</span>
                    <span class="metric-val">{{ msg.metrics.ttft_ms ? `${msg.metrics.ttft_ms} ms` : 'N/A' }}</span>
                  </div>
                  <div class="metric-item" title="Latencia total del ciclo de inferencia">
                    <Zap :size="12" />
                    <span>Latencia:</span>
                    <span class="metric-val">{{ msg.metrics.total_latency_ms ? `${(msg.metrics.total_latency_ms / 1000).toFixed(2)}s` : 'N/A' }}</span>
                  </div>
                  <div class="metric-item" title="Velocidad de generación activa (tokens por segundo)">
                    <Cpu :size="12" />
                    <span>TPS:</span>
                    <span class="metric-val">{{ msg.metrics.tokens_per_second ? `${msg.metrics.tokens_per_second.toFixed(1)} t/s` : 'N/A' }}</span>
                  </div>
                  <div v-if="msg.metrics.compacted" class="metric-item" style="color: var(--text-secondary);" title="La ventana de contexto superó el límite y fue compactada">
                    <span>Contexto Compactado</span>
                  </div>

                  <!-- Tools executed visual log -->
                  <div v-if="msg.metrics.tools_executed && msg.metrics.tools_executed.length > 0" style="display: flex; gap: 6px; flex-wrap: wrap; width: 100%; margin-top: 4px;">
                    <div 
                      v-for="(t, tIdx) in msg.metrics.tools_executed" 
                      :key="tIdx"
                      :class="['tool-execution-badge', { error: t.status === 'ERROR' }]"
                      :title="`Parámetros: ${JSON.stringify(t.parameters)}`"
                    >
                      <Terminal :size="10" />
                      {{ t.name }} ({{ t.status }})
                    </div>
                  </div>
                </div>

              </div>
            </div>

            <!-- Streaming Skeleton / Loading Placeholder -->
            <div v-if="isAgentLoading && messages.length > 0 && messages[messages.length - 1].role === 'user'" class="message-row assistant">
              <div class="message-bubble" style="width: 140px; display: flex; flex-direction: column; gap: 8px;">
                <div class="skeleton" style="height: 14px; width: 100%;"></div>
                <div class="skeleton" style="height: 14px; width: 60%;"></div>
              </div>
            </div>
          </div>

          <!-- Aviso de ubicación (cuando esta bloqueada o fallo): guia al usuario -->
          <div v-if="geoNotice" class="geo-notice">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;">
              <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>
            </svg>
            <span class="geo-notice-text">{{ geoNotice }}</span>
            <button class="geo-notice-btn" @click="retryLocation">Reintentar</button>
            <button class="geo-notice-close" @click="geoNotice = ''" title="Cerrar">✕</button>
          </div>

          <!-- Chat Input Area -->
          <footer class="chat-input-area">
            <div class="chat-input-wrapper">
              <button 
                :class="['btn-voice', { recording: isRecording }]" 
                @click="toggleVoiceRecord"
                :title="isRecording ? 'Detener grabación' : 'Grabar mensaje por voz'"
                :disabled="isAgentLoading"
              >
                <Mic v-if="!isRecording" :size="20" />
                <MicOff v-else :size="20" />
              </button>

              <input 
                v-model="inputMessage" 
                type="text" 
                class="text-input" 
                placeholder="Escribe un mensaje..."
                @keyup.enter="sendMessage"
                :disabled="isAgentLoading"
                ref="inputBox"
              />

              <button 
                class="btn-send" 
                @click="sendMessage" 
                :disabled="!inputMessage.trim() || isAgentLoading"
              >
                <Send :size="18" />
              </button>
            </div>

            <div class="voice-source-bar">
              <span v-if="isRecording" style="color: var(--danger-color); display: flex; align-items: center; gap: 6px; font-weight: 500;">
                <span class="status-dot" style="background-color: var(--danger-color); box-shadow: 0 0 6px var(--danger-color);"></span>
                Grabando audio... Habla ahora.
              </span>
              <span v-else-if="voiceStatus" style="color: var(--text-secondary); font-weight: 500;">
                {{ voiceStatus }}
              </span>
              <span v-else></span>

              <div class="toggle-switch" @click="transcriptionMethod = 'whisper'">
                <span>Transcribir con: <strong>Whisper (Local)</strong></span>
              </div>
            </div>
          </footer>

        </section>

        <!-- Tab 2: Observability Audit Dashboard View -->
        <section v-else class="audit-panel">
          
          <div class="audit-header">
            <button class="btn-hamburger" @click="sidebarOpen = true" title="Abrir menú de chats">
              <Menu :size="22" />
            </button>
            <div>
              <h2>Bitácora de Observabilidad en Acción</h2>
              <p style="color: var(--text-muted); font-size: 0.9rem; margin-top: 4px;">
                Métricas de rendimiento e instrumentación guardadas en la base de datos local SQLite (auditoría en tiempo real).
              </p>
            </div>
            <button class="btn-header" @click="loadAuditData" :disabled="loadingAudit">
              <RefreshCw :class="{ 'spinner-icon': loadingAudit }" :size="16" />
              Actualizar
            </button>
          </div>

          <!-- Diagnostic Warning Banner if endpoints missing -->
          <div v-if="auditError" style="background: rgba(231, 76, 60, 0.08); border: 1px dashed var(--danger-color); border-radius: 12px; padding: 16px; margin-bottom: 24px; color: #ff9999; font-size: 0.9rem; line-height: 1.5; animation: fadeInUp 0.3s ease-out;">
            <div style="font-weight: 700; display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--danger-color);"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
              No se pudo conectar con los endpoints de observabilidad
            </div>
            <div style="margin-bottom: 8px;">{{ auditError }}</div>
            <div>
              <em>Tip: Asegúrate de reiniciar tu servidor FastAPI local en la PC remota donde corre la IA, utilizando el archivo <code>server.py</code> actualizado que acabamos de modificar en <code>entregable-semana-05/src/server.py</code>. Esto registrará los endpoints <code>GET /observability/logs</code> y <code>/stats</code> necesarios.</em>
            </div>
          </div>

          <!-- Stats Cards Grid -->
          <div class="audit-stats-grid">
            <div class="stat-card">
              <span class="stat-card-title">Interacciones Totales</span>
              <span class="stat-card-value">{{ auditStats.total || 0 }}</span>
            </div>
            <div class="stat-card">
              <span class="stat-card-title">Promedio TTFT</span>
              <span class="stat-card-value">{{ auditStats.avg_ttft_ms ? `${auditStats.avg_ttft_ms.toFixed(1)} ms` : 'N/A' }}</span>
            </div>
            <div class="stat-card">
              <span class="stat-card-title">Promedio Latencia</span>
              <span class="stat-card-value">{{ auditStats.avg_latency_ms ? `${(auditStats.avg_latency_ms / 1000).toFixed(2)} s` : 'N/A' }}</span>
            </div>
            <div class="stat-card">
              <span class="stat-card-title">Inyecciones Bloqueadas</span>
              <span class="stat-card-value" style="color: var(--danger-color)">
                {{ auditStats.blocked || 0 }} 
                <span style="font-size: 0.9rem; font-weight: 500; color: var(--text-muted)">
                  ({{ auditStats.total ? ((auditStats.blocked / auditStats.total) * 100).toFixed(1) : 0 }}%)
                </span>
              </span>
            </div>
          </div>

          <!-- Audit Logs Table -->
          <div class="audit-table-wrapper">
            <div v-if="loadingAudit" style="padding: 40px; text-align: center; color: var(--text-muted)">
              <svg class="spinner-icon" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" style="margin-bottom: 12px;">
                <circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle>
                <path d="M4 12a8 8 0 0 1 8-8V4C5.37 4 0 9.37 0 16h4z" fill="currentColor"></path>
              </svg>
              <p>Consultando base de datos de observabilidad SQLite...</p>
            </div>

            <table v-else class="audit-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Fecha / Hora (UTC)</th>
                  <th>Input Usuario</th>
                  <th>TTFT</th>
                  <th>Latencia</th>
                  <th>TPS</th>
                  <th>Guardrail</th>
                  <th>Herramientas Invocadas</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="log in auditLogs" :key="log.id">
                  <td>{{ log.id }}</td>
                  <td style="white-space: nowrap;">{{ formatDateTime(log.timestamp) }}</td>
                  <td style="max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" :title="log.user_prompt">
                    {{ log.user_prompt }}
                  </td>
                  <td>{{ log.ttft_ms ? `${log.ttft_ms} ms` : 'N/A' }}</td>
                  <td>{{ log.total_latency_ms ? `${(log.total_latency_ms / 1000).toFixed(2)}s` : 'N/A' }}</td>
                  <td>{{ log.tokens_per_second ? `${log.tokens_per_second} t/s` : 'N/A' }}</td>
                  <td>
                    <span :class="log.was_blocked ? 'badge-blocked-yes' : 'badge-blocked-no'">
                      {{ log.was_blocked ? 'BLOQUEADO' : 'PASÓ' }}
                    </span>
                  </td>
                  <td style="max-width: 250px;">
                    <div style="display: flex; gap: 4px; flex-wrap: wrap;">
                      <span 
                        v-for="(t, tIdx) in parseTools(log.tools_executed)" 
                        :key="tIdx" 
                        :class="['tool-execution-badge', { error: t.status === 'ERROR' }]"
                        :title="`Parámetros: ${JSON.stringify(t.parameters)}`"
                      >
                        {{ t.name }}
                      </span>
                      <span v-if="parseTools(log.tools_executed).length === 0" style="color: var(--text-muted); font-size: 0.75rem;">
                        Ninguna
                      </span>
                    </div>
                  </td>
                </tr>
                <tr v-if="auditLogs.length === 0">
                  <td colspan="8" style="text-align: center; color: var(--text-muted); padding: 30px;">
                    La bitácora de observabilidad está vacía
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

        </section>

      </main>
    </template>
  </div>
</template>

<script>
import { 
  Plus, 
  Trash2, 
  Activity, 
  LogOut, 
  Clock, 
  Zap, 
  Cpu, 
  Mic, 
  MicOff, 
  Send, 
  ShieldAlert,
  Terminal,
  RefreshCw,
  Menu,
  X,
  Eye,
  EyeOff
} from '@lucide/vue';

export default {
  name: 'App',
  components: {
    Plus,
    Trash2,
    Activity,
    LogOut,
    Clock,
    Zap,
    Cpu,
    Mic,
    MicOff,
    Send,
    ShieldAlert,
    Terminal,
    RefreshCw,
    Menu,
    X,
    Eye,
    EyeOff
  },
  data() {
    return {
      // Configuration
      // URL del backend del AI agent. Reporte de seguridad (A-04): ANTES se leia
      // de localStorage, lo que permitia a un XSS (o al propio usuario) redirigir
      // el backend a un servidor atacante y robar conversaciones/credenciales.
      // AHORA es FIJA en el build: solo VITE_BACKEND_URL (inyectada al compilar)
      // o, en su defecto, el mismo origen. NO configurable en runtime.
      backendUrl: import.meta.env.VITE_BACKEND_URL || window.location.origin,

      // API publica de SwingTails: en desarrollo (npm run dev) usa el proxy de Vite ('')
      // para evitar bloqueos de CORS del navegador hacia Render. En producción usa VITE_API_BASE o la URL de Render.
      apiBase: import.meta.env.VITE_API_BASE || (import.meta.env.DEV ? '' : 'https://swingtails-api-yz02.onrender.com'),
      
      // apiBase: import.meta.env.VITE_API_BASE || (import.meta.env.DEV ? '' : 'http://localhost:3001'),

      // Pantalla de acceso: 'login' | 'register'. El registro se agrego para
      // que gente externa pueda crear su cuenta y probar el agente sin
      // depender de credenciales prestadas.
      authMode: 'login',

      email: '',
      password: '',
      showLoginPassword: false,

      // Formulario de recuperación de contraseña (POST /api/auth/forgot-password)
      forgotEmail: '',
      forgotLoading: false,
      forgotError: '',
      forgotSuccess: '',

      // Formulario de registro (POST /api/auth/register: name, email y password
      // son obligatorios; phone_number es opcional).
      regName: '',
      regEmail: '',
      regPhone: '',
      regPassword: '',
      regPassword2: '',
      showRegPassword: false,
      showRegPassword2: false,
      registerLoading: false,
      registerError: '',
      // Un campo solo muestra su error DESPUES de que el usuario lo toco (blur).
      // Asi el formulario no aparece en rojo desde el primer segundo.
      touched: { name: false, email: false, phone: false, password: false, password2: false },
      // (H-03) El Access Token vive SOLO en memoria (estado de Vue), NUNCA en
      // localStorage: asi un XSS no puede robar la sesion persistente. El Refresh
      // Token lo guarda la Auth API en una cookie HttpOnly (invisible a JS) y con
      // ella renovamos el Access Token al cargar la pagina (silentReauth).
      // Por eso YA NO guardamos email/password en memoria: el refresh usa la
      // cookie, no las credenciales.
      jwt: '',
      manualJwt: '',
      currentUserId: null,
      currentUser: null,
      isLoggedIn: false,
      loginLoading: false,
      loginError: '',

      // Tabs & Sidebar
      activeTab: 'chat', // 'chat' | 'audit'
      sidebarOpen: false, // drawer off-canvas en movil (hamburguesa)
      conversations: [],
      activeConversationId: localStorage.getItem('swingtails_active_conv') || null,
      
      // Messages state
      messages: [],
      inputMessage: '',

      // Agent Streaming Status
      isAgentLoading: false,
      agentPhase: null, // { phase, detail, tool }

      // Geolocalizacion (para "veterinarias mas cercanas"). Se pide permiso al
      // navegador SOLO cuando el usuario pregunta algo de ubicacion; si lo
      // concede, se manda lat/lon en cada mensaje. Si lo niega, marcamos
      // geoDenied para NO volver a molestar (el agente pedira activarlo).
      userLat: null,
      userLon: null,
      geoDenied: false,
      // Evita reintentar la ubicacion en bucle dentro de un mismo mensaje.
      locationRetryDone: false,
      // Aviso visible con instrucciones cuando la ubicacion falla o esta
      // bloqueada por el navegador (el prompt nativo no reaparece si el usuario
      // ya la denego). Se muestra como banner sobre el input del chat.
      geoNotice: '',

      // Voice Settings
      isRecording: false,
      mediaRecorder: null,
      audioChunks: [],
      transcriptionMethod: 'whisper', // 'whisper' | 'webspeech'
      voiceStatus: '',
      speechRecognition: null,

      // Audit log stats
      auditLogs: [],
      auditStats: {},
      loadingAudit: false,
      auditError: ''
    };
  },
  computed: {
    // --- Validaciones del registro -----------------------------------------
    // Los requisitos de la contraseña se verificaron EMPIRICAMENTE contra la
    // API de SwingTails (su respuesta es generica: "La contraseña no cumple con
    // los requisitos de seguridad", sin decir cual falta). Comprobado:
    //   'Passw1!'    (7 chars, todas las clases) -> RECHAZADA  => minimo 8
    //   'Passwo1!'   (8 chars, todas las clases) -> ACEPTADA
    //   'Testing123' (sin simbolo)               -> RECHAZADA
    //   'testing12!' (sin mayuscula)             -> RECHAZADA
    //   'TESTING12!' (sin minuscula)             -> RECHAZADA
    //   'TestingAb!' (sin numero)                -> RECHAZADA
    // Validamos aqui lo mismo para decirle al usuario QUE le falta, en vez de
    // mandarlo a la API a que le rebote un mensaje que no explica nada.
    passwordChecks() {
      const p = this.regPassword || '';
      return [
        { ok: p.length >= 8, label: 'Al menos 8 caracteres' },
        { ok: /[A-Z]/.test(p), label: 'Una letra mayúscula (A-Z)' },
        { ok: /[a-z]/.test(p), label: 'Una letra minúscula (a-z)' },
        { ok: /[0-9]/.test(p), label: 'Un número (0-9)' },
        { ok: /[^A-Za-z0-9]/.test(p), label: 'Un símbolo (! @ # $ % & ...)' }
      ];
    },
    passwordValid() {
      return this.passwordChecks.every(r => r.ok);
    },
    passwordsMatch() {
      return !!this.regPassword && this.regPassword === this.regPassword2;
    },
    nameValid() {
      const n = this.regName.trim();
      // (C-05) Rechaza < y > en el nombre para no enviar payloads XSS
      // (<script>...). Vue ya escapa al renderizar, pero validamos en origen.
      return n.length >= 3 && !/[<>]/.test(this.regName);
    },
    emailValid() {
      return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(this.regEmail.trim());
    },
    // El telefono es OPCIONAL: vacio es valido; si se escribe, 10 digitos.
    phoneValid() {
      const t = this.regPhone.trim();
      return !t || /^\d{10}$/.test(t);
    },
    registerFormValid() {
      return this.nameValid && this.emailValid && this.phoneValid
        && this.passwordValid && this.passwordsMatch;
    },

    userNameLetter() {
      if (this.currentUser && this.currentUser.name) {
        return String(this.currentUser.name).charAt(0).toUpperCase();
      }
      return this.currentUserId ? String(this.currentUserId).charAt(0).toUpperCase() : 'U';
    },
    chatTitle() {
      if (!this.activeConversationId) return 'Nuevo Chat';
      const active = this.conversations.find(c => c.conversation_id === this.activeConversationId);
      return active ? active.title : 'Chat Veterinario';
    }
  },
  watch: {
    // (A-04) Se elimino el watcher que persistia backendUrl en localStorage:
    // la URL del backend ya es fija en el build y no debe poder cambiarse.
    activeTab(newTab) {
      if (newTab === 'audit') {
        this.loadAuditData();
      }
    }
  },
  async mounted() {
    // (H-03) Auto-login SIN localStorage: al cargar la pagina intentamos renovar
    // el Access Token usando el Refresh Token que la Auth API guardo en una
    // cookie HttpOnly. Si funciona, entramos directo; si no (sin cookie valida),
    // se muestra la pantalla de login. Asi la sesion persiste tras F5 sin dejar
    // el token en disco.
    await this.silentReauth(true);

    // Auto focus text input on start
    this.$nextTick(() => {
      this.focusInput();
    });
  },
  methods: {
    focusInput() {
      if (this.$refs.inputBox) {
        this.$refs.inputBox.focus();
      }
    },
    // Cambia entre "Iniciar sesión" y "Crear cuenta" limpiando los errores del
    // formulario anterior (si no, el error viejo queda colgado en la otra vista).
    switchAuthMode(mode) {
      this.authMode = mode;
      this.loginError = '';
      this.registerError = '';
      this.forgotError = '';
      this.forgotSuccess = '';
      this.forgotLoading = false;
      this.showLoginPassword = false;
      this.showRegPassword = false;
      this.showRegPassword2 = false;
      if (mode === 'forgot' && this.email) {
        this.forgotEmail = this.email;
      }
      // Reinicia los "tocados": al volver al registro no debe aparecer en rojo.
      Object.keys(this.touched).forEach(k => { this.touched[k] = false; });
    },

    // Solicitud de restablecimiento de contraseña
    async handleForgotPassword() {
      this.forgotError = '';
      this.forgotSuccess = '';

      const targetEmail = (this.forgotEmail || '').trim();
      if (!targetEmail) {
        this.forgotError = 'Escribe tu correo electrónico.';
        return;
      }

      this.forgotLoading = true;
      try {
        const response = await fetch(`${this.apiBase}/api/auth/forgot-password`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: targetEmail })
        });

        const resData = await response.json();
        if (!response.ok || resData.status === 'error') {
          throw new Error(resData.message || 'No se pudo enviar el correo de recuperación.');
        }

        this.forgotSuccess = resData.message || 'Se ha enviado un correo con las instrucciones para restablecer tu contraseña.';
      } catch (err) {
        if (err.name === 'TypeError' && err.message === 'Failed to fetch') {
          this.forgotError = 'No se pudo conectar con el servidor. Verifica tu conexión a internet o intenta más tarde.';
        } else {
          this.forgotError = err.message;
        }
      } finally {
        this.forgotLoading = false;
      }
    },

    // Registro de un usuario NUEVO contra la API de SwingTails.
    //
    // Contrato real verificado contra la API (diverge del Swagger):
    //   POST /api/auth/register  {name, email, password[, phone_number]}
    //   ok    -> {status:'success', data:{user:{...}}}   <-- NO devuelve token
    //   error -> {status:'error', message:'El email ya se encuentra registrado'}
    //
    // Como no entrega token, tras registrar hacemos LOGIN AUTOMATICO reutilizando
    // handleLogin(): asi el usuario entra directo al chat sin escribir dos veces
    // sus datos.
    async handleRegister() {
      this.registerError = '';

      // Red de seguridad: el boton ya esta deshabilitado si el formulario no es
      // valido, pero si alguien envia con Enter marcamos todo como "tocado"
      // para que se vean los errores de cada campo.
      if (!this.registerFormValid) {
        Object.keys(this.touched).forEach(k => { this.touched[k] = true; });
        return;
      }

      this.registerLoading = true;
      try {
        const body = {
          // (M-01) Sanitizacion por LISTA BLANCA: solo letras (con acentos),
          // numeros, espacios, punto y guion. Elimina TODO lo demas (< > " ' &
          // ( ) ; / \ etc.) para no enviar HTML/JS ni caracteres peligrosos.
          // Nota: la sanitizacion real debe hacerla tambien el backend (Auth API).
          name: this.regName.trim().replace(/[^\p{L}\p{N}\s.\-]/gu, '').slice(0, 80),
          email: this.regEmail.trim(),
          password: this.regPassword
        };
        if (this.regPhone.trim()) body.phone_number = this.regPhone.trim();

        const response = await fetch(`${this.apiBase}/api/auth/register`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });

        const resData = await response.json();
        if (!response.ok || resData.status === 'error') {
          const apiMsg = resData.message || 'No se pudo crear la cuenta.';
          // La API responde un generico "no cumple con los requisitos de
          // seguridad" sin decir cual falta. Si llegara aqui (p.ej. si el
          // backend endurece la regla), enumeramos los requisitos.
          if (/requisitos de seguridad/i.test(apiMsg)) {
            throw new Error(
              'La contraseña no cumple los requisitos: mínimo 8 caracteres, ' +
              'una mayúscula, una minúscula, un número y un símbolo.'
            );
          }
          throw new Error(apiMsg);
        }

        // (H-03) Cuenta creada -> iniciamos sesion con LOGIN normal para que la
        // Auth API entregue la cookie HttpOnly del Refresh Token (el endpoint de
        // registro podria no setearla). El Access Token queda solo en memoria.
        this.email = body.email;
        this.password = this.regPassword;
        await this.handleLogin();

        if (this.isLoggedIn) {
          this.regName = this.regEmail = this.regPhone = '';
          this.regPassword = this.regPassword2 = '';
        } else {
          this.authMode = 'login';
          this.loginError = 'Tu cuenta se creó correctamente. Inicia sesión para continuar.';
        }
      } catch (err) {
        if (err.name === 'TypeError' && err.message === 'Failed to fetch') {
          this.registerError = 'No se pudo procesar el registro. Intenta de nuevo en unos segundos.';
        } else {
          this.registerError = err.message;
        }
      } finally {
        this.registerLoading = false;
      }
    },

    // Authentication handlers
    async handleLogin() {
      this.loginLoading = true;
      this.loginError = '';
      try {
        const response = await fetch(`${this.apiBase}/api/auth/login`, {
          method: 'POST',
          // (H-03) credentials:'include' permite que el navegador GUARDE la
          // cookie HttpOnly del Refresh Token que envia la Auth API (Set-Cookie).
          credentials: 'include',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            email: this.email,
            password: this.password
          })
        });

        const resData = await response.json();
        if (!response.ok || resData.status === 'error') {
          throw new Error(resData.message || 'Error de credenciales en SwingTails');
        }

        const dataObj = resData.data || {};
        const token = dataObj.accessToken || resData.accessToken || dataObj.token || resData.token;
        if (!token) {
          throw new Error('No se devolvió un token de acceso desde el servidor');
        }

        // (H-03) El Refresh Token viaja en la cookie HttpOnly (NO se toca desde
        // JS). El Access Token se guarda SOLO en memoria (setLoginSession).
        this.setLoginSession(token);
      } catch (err) {
        if (err.name === 'TypeError' && err.message === 'Failed to fetch') {
          this.loginError = 'No se pudo conectar con la API de SwingTails (Failed to fetch). Verifica tu conexión o intenta en unos segundos (Render cold start).';
        } else {
          this.loginError = err.message;
        }
      } finally {
        this.loginLoading = false;
      }
    },

    mapUserFromToken(token) {
      try {
        const base64Url = token.split('.')[1];
        const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
        const jsonPayload = decodeURIComponent(atob(base64).split('').map(c => {
          return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
        }).join(''));
        
        const payload = JSON.parse(jsonPayload);
        
        // Similar mapping function to userResponseToEntity in Dart
        return {
          id: payload.id || payload.userId || payload.user_id,
          name: payload.name || 'Usuario',
          email: payload.email || '',
          phone: payload.phone_number || payload.phone || null,
          imageUrl: payload.image_url || payload.imageUrl || null,
          role: payload.role || null,
          address: payload.street ? {
            street: payload.street,
            exteriorNumber: payload.exterior_number,
            neighborhood: payload.neighborhood,
            postalCode: payload.postal_code,
            city: payload.city,
            state: payload.state
          } : null
        };
      } catch (e) {
        console.error('Error decoding user from token:', e);
        return null;
      }
    },
    decodeAndValidateManualJwt(token) {
      const user = this.mapUserFromToken(token);
      if (user && user.id) {
        this.currentUser = user;
        this.currentUserId = user.id;
        this.setLoginSession(token);
      } else {
        this.loginError = 'Formato de JWT inválido o no contiene información de usuario';
        this.isLoggedIn = false;
        this.jwt = '';
      }
    },
    setLoginSession(token) {
      // (H-03) Access Token SOLO en memoria; nunca en localStorage.
      this.jwt = token;

      const user = this.mapUserFromToken(token);
      if (user && user.id) {
        this.currentUser = user;
        this.currentUserId = user.id;
      } else {
        // (M-02) Antes se ponia currentUserId = 99 (un id falso que podia
        // colisionar con un usuario real). Si el token no trae id valido, el
        // token esta roto -> no iniciamos sesion, forzamos reautenticacion.
        this.forceReauth('No se pudo validar tu sesión. Inicia sesión de nuevo.');
        return;
      }

      this.isLoggedIn = true;
      this.loadConversationsList();

      if (this.activeConversationId) {
        this.loadConversation(this.activeConversationId);
      }
      
      this.$nextTick(() => {
        this.focusInput();
      });
    },
    handleLogout() {
      // (H-03) Pide a la Auth API que INVALIDE la cookie HttpOnly del Refresh
      // Token (Set-Cookie de borrado). credentials:'include' envia la cookie.
      // Es fire-and-forget: no bloqueamos el cierre de sesion si la red falla.
      try {
        fetch(`${this.apiBase}/api/auth/logout`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
        }).catch(() => {});
      } catch (e) { /* ignore */ }

      this.isLoggedIn = false;
      this.jwt = '';
      this.currentUser = null;
      this.currentUserId = null;
      this.messages = [];
      this.conversations = [];
      this.activeConversationId = null;
      this.password = '';
      // Ya no guardamos el token en localStorage; solo limpiamos el resto.
      localStorage.removeItem('swingtails_active_conv');
    },

    // ¿El JWT ya venció? (lee el claim `exp`; margen de 30s de skew).
    // Los access tokens de SwingTails duran ~30 min y hoy NO hay refresh
    // funcional en la API, así que al vencer hay que reautenticar.
    isJwtExpired(token) {
      if (!token) return true;
      try {
        const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
        const payload = JSON.parse(atob(base64));
        if (!payload.exp) return false; // sin exp: no lo bloqueamos
        return Date.now() / 1000 > payload.exp + 30;
      } catch (e) {
        return false;
      }
    },

    // (H-03) Renovacion SILENCIOSA del Access Token usando el Refresh Token de
    // la cookie HttpOnly. NO usa email/password. Devuelve true si obtuvo un token
    // valido. `fullSetup=true` (al cargar la app) ademas entra a la sesion y
    // carga las conversaciones; en caliente (retry por 401) solo renueva el token
    // sin recargar la vista para no interrumpir un mensaje en curso.
    async silentReauth(fullSetup = false) {
      try {
        const response = await fetch(`${this.apiBase}/api/auth/refresh-token`, {
          method: 'POST',
          credentials: 'include',            // envia la cookie HttpOnly del refresh
          headers: { 'Content-Type': 'application/json' },
        });
        if (!response.ok) return false;
        const resData = await response.json();
        if (resData.status === 'error') return false;
        const dataObj = resData.data || {};
        const token = dataObj.accessToken || resData.accessToken || dataObj.token || resData.token;
        if (!token || this.isJwtExpired(token)) return false;

        if (fullSetup) {
          // Primer arranque: sesion completa (decodifica usuario, carga chats).
          this.setLoginSession(token);
        } else {
          // Renovacion en caliente: solo el token en memoria.
          this.jwt = token;
          const user = this.mapUserFromToken(token);
          if (user && user.id) { this.currentUser = user; this.currentUserId = user.id; }
          this.isLoggedIn = true;
        }
        return true;
      } catch (e) {
        console.error('Silent re-auth falló:', e);
        return false;
      }
    },

    // Sesión vencida: cierra sesión y muestra la pantalla de login con aviso.
    forceReauth(message) {
      this.handleLogout();
      this.loginError = message || 'Tu sesión expiró. Inicia sesión de nuevo.';
    },

    // Conversations management
    async loadConversationsList() {
      try {
        const response = await fetch(`${this.backendUrl}/conversations`, {
          headers: {
            'Authorization': `Bearer ${this.jwt}`,
            'ngrok-skip-browser-warning': 'true'
          }
        });
        if (response.ok) {
          this.conversations = await response.json();
        }
      } catch (err) {
        console.error('Error cargando historial de chats:', err);
      }
    },
    async loadConversation(conversationId) {
      this.sidebarOpen = false; // cerrar drawer en movil al elegir chat
      this.activeConversationId = conversationId;
      localStorage.setItem('swingtails_active_conv', conversationId);
      this.messages = [];
      this.isAgentLoading = true;
      this.agentPhase = { phase: 'thinking', detail: 'Recuperando historial...' };

      try {
        const response = await fetch(`${this.backendUrl}/conversations/${conversationId}`, {
          headers: {
            'Authorization': `Bearer ${this.jwt}`,
            'ngrok-skip-browser-warning': 'true'
          }
        });
        if (!response.ok) throw new Error('Error al cargar conversación');
        const data = await response.json();
        
        // Map messages to bubble structure
        // Backend returns: list of {"role": "user"|"assistant", "content": "..."}
        this.messages = data.messages.map(m => ({
          role: m.role,
          content: m.content,
          blocked: false,
          metrics: null // Metricas solo persisten en observabilidad local en BD
        }));
      } catch (err) {
        console.error(err);
      } finally {
        this.isAgentLoading = false;
        this.agentPhase = null;
        this.scrollToBottom();
      }
    },
    startNewConversation() {
      this.sidebarOpen = false; // cerrar drawer en movil
      this.activeConversationId = null;
      localStorage.removeItem('swingtails_active_conv');
      this.messages = [];
      this.focusInput();
    },
    async deleteConversation(conversationId) {
      if (!confirm('¿Seguro que deseas eliminar este chat?')) return;
      try {
        const response = await fetch(`${this.backendUrl}/conversations/${conversationId}`, {
          method: 'DELETE',
          headers: {
            'Authorization': `Bearer ${this.jwt}`,
            'ngrok-skip-browser-warning': 'true'
          }
        });
        if (response.ok) {
          if (this.activeConversationId === conversationId) {
            this.startNewConversation();
          }
          this.loadConversationsList();
        }
      } catch (err) {
        console.error('Error al borrar conversación:', err);
      }
    },
    clearActiveChatMessages() {
      this.messages = [];
    },

    // Suggestion card clicked
    applySuggestion(text) {
      this.inputMessage = text;
      this.sendMessage();
    },

    // ¿El mensaje pide veterinarias por cercania/ubicacion? Si es asi, conviene
    // pedir el permiso de ubicacion ANTES de enviar, para que el agente pueda
    // calcular las clinicas cercanas en el mismo turno.
    looksLikeLocationIntent(text) {
      return /cercan|cerca de m|mas cerca|m[aá]s pr[oó]xim|cerca de aqu|por mi ubicaci|mi ubicaci|junto a m[ií]/i
        .test(text || '');
    },

    // Pide la ubicacion al navegador. Devuelve {lat, lon} o null, y en caso de
    // fallo deja un aviso ACCIONABLE en geoNotice (el banner). Clave: si el
    // usuario ya bloqueo la ubicacion, el navegador NO vuelve a mostrar el
    // prompt; hay que detectarlo (Permissions API) y decirle como desbloquearla.
    async ensureLocation() {
      if (this.userLat != null && this.userLon != null) {
        return { lat: this.userLat, lon: this.userLon };
      }
      if (!('geolocation' in navigator)) {
        this.geoNotice = 'Tu navegador no soporta geolocalización.';
        return null;
      }
      // La geolocalización solo funciona en contexto seguro (https o localhost).
      if (window.isSecureContext === false) {
        this.geoNotice = 'La ubicación solo funciona con conexión segura (https). '
          + 'Abre la app desde su enlace https, no por la IP local.';
        return null;
      }
      // ¿Ya está bloqueada? Entonces el prompt NO va a aparecer: guiamos al usuario.
      try {
        if (navigator.permissions && navigator.permissions.query) {
          const st = await navigator.permissions.query({ name: 'geolocation' });
          if (st.state === 'denied') {
            this.geoDenied = true;
            this.geoNotice = 'La ubicación está BLOQUEADA para este sitio. Haz clic en el '
              + 'candado 🔒 (o el ícono ⚙/ⓘ) junto a la dirección → Ubicación → Permitir, '
              + 'y luego recarga la página.';
            return null;
          }
        }
      } catch (e) { /* Permissions API no disponible: seguimos al getCurrentPosition */ }

      return await new Promise((resolve) => {
        navigator.geolocation.getCurrentPosition(
          (pos) => {
            this.userLat = pos.coords.latitude;
            this.userLon = pos.coords.longitude;
            this.geoNotice = '';
            resolve({ lat: this.userLat, lon: this.userLon });
          },
          (err) => {
            if (err && err.code === 1) {          // PERMISSION_DENIED
              this.geoDenied = true;
              this.geoNotice = 'Bloqueaste la ubicación para este sitio. Haz clic en el candado '
                + '🔒 junto a la dirección → Ubicación → Permitir, y recarga la página.';
            } else if (err && err.code === 2) {   // POSITION_UNAVAILABLE
              this.geoNotice = 'No se pudo obtener tu ubicación. Verifica que la ubicación del '
                + 'dispositivo (GPS/servicios de ubicación) esté activada.';
            } else {                               // TIMEOUT u otro
              this.geoNotice = 'La ubicación tardó demasiado en responder. Usa "Reintentar".';
            }
            resolve(null);
          },
          { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 }
        );
      });
    },

    // Botón "Reintentar" del banner: reintenta la ubicacion (gesto del usuario,
    // lo mas fiable para que aparezca el prompt) y, si la consigue, reenvia la
    // ultima pregunta del usuario.
    async retryLocation() {
      this.geoDenied = false;
      this.geoNotice = '';
      const loc = await this.ensureLocation();
      if (loc) {
        const lastUser = [...this.messages].reverse().find(m => m.role === 'user');
        if (lastUser && lastUser.content && !this.isAgentLoading) {
          this.inputMessage = lastUser.content;
          this.sendMessage(true);
        }
      }
    },

    // El backend indico que hace falta la ubicacion. Pide el permiso del
    // navegador y, si se concede, reenvia la misma pregunta con lat/lon.
    async tryLocationRetry(assistantIndex) {
      if (this.locationRetryDone || this.geoDenied) return;
      // Si YA teniamos coordenadas y aun asi pidio ubicacion, no insistir (evita
      // bucle): es un fallo real, no falta de permiso.
      if (this.userLat != null && this.userLon != null) return;

      const userMsg = this.messages[assistantIndex - 1];
      if (!userMsg || userMsg.role !== 'user' || !userMsg.content) return;

      this.locationRetryDone = true;
      const loc = await this.ensureLocation();   // muestra el prompt del navegador
      if (loc) {
        this.inputMessage = userMsg.content;
        this.sendMessage(true);                   // reintento con coordenadas
      }
    },

    // Sending messages (SSE integration)
    async sendMessage(isRetry = false) {
      const text = this.inputMessage.trim();
      if (!text || this.isAgentLoading) return;

      // Mensaje nuevo (no un reintento): permite un nuevo intento de ubicacion.
      if (!isRetry) this.locationRetryDone = false;

      // Si el mensaje es de ubicacion y aun no tenemos coordenadas, pedimos el
      // permiso ANTES de enviar (el navegador muestra su prompt nativo). Si el
      // usuario lo concede rapido, este mismo turno ya lleva lat/lon.
      if (!isRetry && this.looksLikeLocationIntent(text)) {
        await this.ensureLocation();
      }

      // Sesión vencida: intentamos re-login silencioso; si no hay credenciales
      // en memoria (o falla), recién ahí pedimos al usuario iniciar sesión.
      if (this.isJwtExpired(this.jwt)) {
        const ok = await this.silentReauth();
        if (!ok) {
          this.forceReauth('Tu sesión expiró. Inicia sesión de nuevo para continuar.');
          return;
        }
      }

      this.inputMessage = '';
      
      // Append user message
      this.messages.push({
        role: 'user',
        content: text,
        blocked: false
      });

      this.scrollToBottom();
      
      this.isAgentLoading = true;
      this.agentPhase = { phase: 'searching', detail: 'Iniciando búsqueda...' };

      // Initialize assistant empty response to stream into
      const assistantMessageIndex = this.messages.length;
      this.messages.push({
        role: 'assistant',
        content: '',
        blocked: false,
        metrics: null
      });

      try {
        const response = await fetch(`${this.backendUrl}/chat/stream`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${this.jwt}`,
            'ngrok-skip-browser-warning': 'true'
          },
          body: JSON.stringify({
            message: text,
            conversation_id: this.activeConversationId || undefined,
            // Ubicacion solo si el usuario la concedio (si no, van null y el
            // agente pedira activar el permiso cuando haga falta).
            lat: this.userLat != null ? this.userLat : undefined,
            lon: this.userLon != null ? this.userLon : undefined
          })
        });

        if (response.status === 401) {
          // Token vencido durante el envío: quitamos el placeholder y probamos
          // un re-login silencioso; si funciona, reintentamos UNA vez.
          this.messages.splice(assistantMessageIndex, 1);
          this.isAgentLoading = false;
          this.agentPhase = null;
          if (!isRetry && await this.silentReauth()) {
            this.inputMessage = text;
            return this.sendMessage(true);
          }
          this.forceReauth('Tu sesión expiró. Inicia sesión de nuevo para continuar.');
          return;
        }
        if (!response.ok) {
          const errText = await response.text();
          throw new Error(errText || `Fallo del Servidor (${response.status})`);
        }

        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = '';

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += value;

          const parts = buffer.split('\n\n');
          buffer = parts.pop() || '';

          for (const part of parts) {
            if (!part.trim()) continue;

            let eventName = '';
            let dataStr = '';

            const lines = part.split('\n');
            for (const line of lines) {
              if (line.startsWith('event:')) {
                eventName = line.slice(6).trim();
              } else if (line.startsWith('data:')) {
                dataStr = line.slice(5).trim();
              }
            }

            if (dataStr) {
              try {
                const data = JSON.parse(dataStr);
                this.handleSseEvent(assistantMessageIndex, eventName, data);
              } catch (err) {
                console.error('Error parseando JSON SSE:', err, dataStr);
              }
            }
          }
        }
      } catch (err) {
        console.error(err);
        this.messages[assistantMessageIndex].content = `Error de Conexión: No se pudo contactar con la IA en ${this.backendUrl}. Detalle: ${err.message}`;
        this.isAgentLoading = false;
        this.agentPhase = null;
      } finally {
        this.scrollToBottom();
      }
    },
    handleSseEvent(msgIndex, event, data) {
      const msg = this.messages[msgIndex];
      if (!msg) return;

      switch (event) {
        case 'phase':
          // Update visual phase loading indicator
          this.agentPhase = {
            phase: data.phase,
            detail: data.detail,
            tool: data.tool || null
          };
          break;

        case 'token':
          // Typing effect: Append token to text content
          this.agentPhase = { phase: 'generating', detail: 'Transmitiendo respuesta...' };
          msg.content += data.text;
          this.scrollToBottom();
          break;

        case 'blocked':
          // Guardrail triggered!
          msg.blocked = true;
          msg.content = data.message;
          this.isAgentLoading = false;
          this.agentPhase = null;
          break;

        case 'done':
          // Interaction completed
          this.isAgentLoading = false;
          this.agentPhase = null;

          if (data.blocked) {
            msg.blocked = true;
          } else {
            // Save metrics to display
            msg.metrics = {
              ttft_ms: data.ttft_ms,
              total_latency_ms: data.total_latency_ms,
              tokens_per_second: data.tokens_per_second,
              compacted: data.compacted,
              tools_executed: data.tools_executed || []
            };
          }

          // Save conversation_id for subsequent turns
          if (data.conversation_id && !this.activeConversationId) {
            this.activeConversationId = data.conversation_id;
            localStorage.setItem('swingtails_active_conv', data.conversation_id);
          }

          // Reload sidebars to reflect new/updated conversations
          this.loadConversationsList();
          this.scrollToBottom();
          this.$nextTick(() => {
            this.focusInput();
          });

          // El agente necesito la ubicacion (una tool devolvio necesita_ubicacion)
          // pero no la teniamos: pedimos el permiso al navegador y, si se concede,
          // reenviamos la MISMA pregunta ya con las coordenadas. Asi el prompt de
          // ubicacion aparece justo cuando de verdad hace falta, sin depender de
          // adivinar por el texto del usuario.
          if (data.needs_location) {
            this.tryLocationRetry(msgIndex);
          }
          break;

        case 'error':
          msg.content += `\n[Error en streaming: ${data.message}]`;
          this.isAgentLoading = false;
          this.agentPhase = null;
          break;
      }
    },

    // Voice recording & Speech-to-Text
    toggleVoiceRecord() {
      if (this.isRecording) {
        this.stopRecording();
      } else {
        this.startRecording();
      }
    },
    async startRecording() {
      this.voiceStatus = '';
      if (this.transcriptionMethod === 'webspeech') {
        this.startWebSpeechRecognition();
        return;
      }

      // Local Whisper Recording via Backend
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        this.voiceStatus = 'Grabación de audio no soportada en este navegador';
        return;
      }

      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        this.audioChunks = [];
        this.mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
        
        this.mediaRecorder.ondataavailable = (event) => {
          if (event.data.size > 0) {
            this.audioChunks.push(event.data);
          }
        };

        this.mediaRecorder.onstop = () => {
          const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
          this.sendAudioToBackend(audioBlob);
          
          // Close media inputs
          stream.getTracks().forEach(track => track.stop());
        };

        this.mediaRecorder.start();
        this.isRecording = true;
      } catch (err) {
        console.error(err);
        this.voiceStatus = 'Acceso al micrófono denegado.';
      }
    },
    stopRecording() {
      if (this.transcriptionMethod === 'webspeech') {
        this.stopWebSpeechRecognition();
        return;
      }

      if (this.mediaRecorder && this.isRecording) {
        this.mediaRecorder.stop();
        this.isRecording = false;
      }
    },
    async sendAudioToBackend(blob) {
      this.voiceStatus = 'Transcribiendo audio con Whisper local...';
      try {
        const formData = new FormData();
        formData.append('audio', blob, 'recording.webm');

        const response = await fetch(`${this.backendUrl}/transcribe`, {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${this.jwt}`,
            'ngrok-skip-browser-warning': 'true'
          },
          body: formData
        });

        if (!response.ok) {
          if (response.status === 503) {
            throw new Error('Whisper no está disponible en este momento. Intenta en unos momentos.');
          }
          throw new Error(`Error en transcripción (${response.status})`);
        }

        const data = await response.json();
        if (data.text && data.text.trim()) {
          this.inputMessage = data.text;
          this.voiceStatus = `Transcrito con éxito (${data.duration_ms}ms)`;
          this.focusInput();
        } else {
          this.voiceStatus = 'No se detectó voz clara.';
        }
      } catch (err) {
        console.error(err);
        this.voiceStatus = err.message;
      }
    },

    // Web Speech API client fallback
    startWebSpeechRecognition() {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRecognition) {
        this.voiceStatus = 'Web Speech API no está soportada en tu navegador (usa Chrome o Safari).';
        return;
      }

      try {
        this.speechRecognition = new SpeechRecognition();
        this.speechRecognition.lang = 'es-MX';
        this.speechRecognition.interimResults = false;
        this.speechRecognition.maxAlternatives = 1;

        this.speechRecognition.onstart = () => {
          this.isRecording = true;
        };

        this.speechRecognition.onerror = (event) => {
          console.error('Speech error:', event.error);
          this.voiceStatus = `Error Speech API: ${event.error}`;
          this.isRecording = false;
        };

        this.speechRecognition.onend = () => {
          this.isRecording = false;
        };

        this.speechRecognition.onresult = (event) => {
          const resultText = event.results[0][0].transcript;
          if (resultText) {
            this.inputMessage = resultText;
            this.voiceStatus = 'Dictado finalizado';
            this.focusInput();
          }
        };

        this.speechRecognition.start();
      } catch (err) {
        console.error(err);
        this.voiceStatus = 'Fallo al inicializar dictado';
        this.isRecording = false;
      }
    },
    stopWebSpeechRecognition() {
      if (this.speechRecognition) {
        this.speechRecognition.stop();
        this.isRecording = false;
      }
    },

    // Audit logs fetcher
    async loadAuditData() {
      this.loadingAudit = true;
      this.auditError = '';
      try {
        // Parallel requests
        const [logsResponse, statsResponse] = await Promise.all([
          fetch(`${this.backendUrl}/observability/logs?limit=50`, {
            headers: {
              'Authorization': `Bearer ${this.jwt}`,
              'ngrok-skip-browser-warning': 'true'
            }
          }),
          fetch(`${this.backendUrl}/observability/stats`, {
            headers: {
              'Authorization': `Bearer ${this.jwt}`,
              'ngrok-skip-browser-warning': 'true'
            }
          })
        ]);

        if (!logsResponse.ok) {
          if (logsResponse.status === 404) {
            throw new Error('El endpoint /observability/logs devolvió error 404 (Not Found). El backend remoto no parece estar ejecutando el código actualizado.');
          }
          throw new Error(`HTTP ${logsResponse.status} al obtener logs.`);
        }
        if (!statsResponse.ok) {
          if (statsResponse.status === 404) {
            throw new Error('El endpoint /observability/stats devolvió error 404 (Not Found). El backend remoto no parece estar ejecutando el código actualizado.');
          }
          throw new Error(`HTTP ${statsResponse.status} al obtener estadísticas.`);
        }

        this.auditLogs = await logsResponse.json();
        this.auditStats = await statsResponse.json();
      } catch (err) {
        console.error('Error fetching audit logs:', err);
        this.auditError = err.message;
        this.auditLogs = [];
        this.auditStats = {};
      } finally {
        this.loadingAudit = false;
      }
    },

    // Helpers
    parseTools(toolsStr) {
      if (!toolsStr) return [];
      try {
        return typeof toolsStr === 'string' ? JSON.parse(toolsStr) : toolsStr;
      } catch (e) {
        return [];
      }
    },

    // Renderiza el markdown que emite la IA a HTML SEGURO. Primero escapa todo
    // el HTML (así el modelo no puede inyectar etiquetas) y luego solo inserta
    // las etiquetas que nosotros generamos. Cubre: negritas, cursivas, código,
    // listas numeradas y con viñetas, encabezados, enlaces y párrafos.
    renderMarkdown(text) {
      if (!text) return '';
      const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const inline = (s) => s
        .replace(/`([^`]+)`/g, '<code>$1</code>')                         // `código`
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')               // **negrita**
        .replace(/__([^_]+)__/g, '<strong>$1</strong>')                   // __negrita__
        .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')         // *cursiva*
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,                  // [texto](url)
          '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

      const lines = esc(text).split('\n');
      let html = '';
      let listType = null;               // 'ul' | 'ol' | null
      let para = [];
      const closeList = () => { if (listType) { html += `</${listType}>`; listType = null; } };
      const flushPara = () => { if (para.length) { html += `<p>${inline(para.join(' '))}</p>`; para = []; } };

      for (const raw of lines) {
        const line = raw.trim();
        if (!line) { flushPara(); closeList(); continue; }
        let m;
        if ((m = line.match(/^#{1,6}\s+(.*)$/))) {          // # encabezado
          flushPara(); closeList(); html += `<h4>${inline(m[1])}</h4>`;
        } else if ((m = line.match(/^\d+[.)]\s+(.*)$/))) {  // 1. lista numerada
          flushPara();
          if (listType !== 'ol') { closeList(); html += '<ol>'; listType = 'ol'; }
          html += `<li>${inline(m[1])}</li>`;
        } else if ((m = line.match(/^[-*•]\s+(.*)$/))) {    // * o - viñeta
          flushPara();
          if (listType !== 'ul') { closeList(); html += '<ul>'; listType = 'ul'; }
          html += `<li>${inline(m[1])}</li>`;
        } else {                                            // texto normal
          closeList(); para.push(line);
        }
      }
      flushPara(); closeList();
      return html;
    },
    scrollToBottom() {
      this.$nextTick(() => {
        const box = this.$refs.messagesBox;
        if (box) {
          box.scrollTop = box.scrollHeight;
        }
      });
    },
    formatDate(dateStr) {
      if (!dateStr) return '';
      try {
        const d = new Date(dateStr);
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
      } catch (e) {
        return dateStr;
      }
    },
    formatDateTime(dateStr) {
      if (!dateStr) return '';
      try {
        const d = new Date(dateStr);
        return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' ' + d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
      } catch (e) {
        return dateStr;
      }
    }
  }
};
</script>

<style>
/* Global CSS transitions and imports. Component layout handles locally scoped. */
</style>
