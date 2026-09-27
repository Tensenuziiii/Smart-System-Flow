"use client";
import { useEffect, useRef, useState } from 'react';
import { PublicClientApplication } from '@azure/msal-browser';
import { apiUrl } from '../lib/api';
import ArchitectureGraph from '../components/ArchitectureGraph';
import FeatureTrace from '../components/FeatureTrace';
import RepoAssistant from '../components/RepoAssistant';
import { ArrowRight, ArrowUpRight, Eye, EyeOff, GitBranch, Lightbulb, Link2, Lock, LogOut, Mail, MessageSquare, Network, Search, ShieldCheck, Sparkles, Sun, Users } from 'lucide-react';

const SESSION_KEY = 'devonboard.session';

function loadSession() {
  if (typeof window === 'undefined') return null;
  try {
    const stored = window.localStorage.getItem(SESSION_KEY) || window.sessionStorage.getItem(SESSION_KEY);
    return stored ? JSON.parse(stored) : null;
  } catch {
    return null;
  }
}

export default function Dashboard() {
  const [session, setSession] = useState(null);
  const [sessionReady, setSessionReady] = useState(false);

  useEffect(() => {
    setSession(loadSession());
    setSessionReady(true);
  }, []);

  const signIn = (accessToken, rememberMe, provider = 'demo') => {
    const nextSession = { accessToken, provider };
    window.localStorage.removeItem(SESSION_KEY);
    window.sessionStorage.removeItem(SESSION_KEY);
    (rememberMe ? window.localStorage : window.sessionStorage).setItem(SESSION_KEY, JSON.stringify(nextSession));
    setSession(nextSession);
  };

  const signOut = () => {
    window.localStorage.removeItem(SESSION_KEY);
    window.sessionStorage.removeItem(SESSION_KEY);
    setSession(null);
  };

  if (!sessionReady) return <div className="session-loading" aria-busy="true" />;
  return session ? <Workspace session={session} onSignOut={signOut} /> : <LoginScreen onSignIn={signIn} />;
}

function LoginScreen({ onSignIn }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [status, setStatus] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [oauthBusy, setOauthBusy] = useState(false);
  const googleButtonRef = useRef(null);
  const oauthActionRef = useRef(null);
  const googleClientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;
  const microsoftClientId = process.env.NEXT_PUBLIC_MICROSOFT_CLIENT_ID;
  const microsoftTenantId = process.env.NEXT_PUBLIC_MICROSOFT_TENANT_ID;

  const exchangeProviderToken = async (provider, idToken) => {
    setOauthBusy(true);
    setStatus('Verifying sign-in...');
    try {
      const response = await fetch(apiUrl(`/v1/auth/oauth/${provider}`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id_token: idToken })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `${provider} sign-in could not be verified.`);
      if (!data.access_token) throw new Error('The server did not return a session token.');
      onSignIn(data.access_token, rememberMe, provider);
    } catch (error) {
      setStatus(error.message || `Unable to complete ${provider} sign-in.`);
      setOauthBusy(false);
    }
  };

  oauthActionRef.current = exchangeProviderToken;

  useEffect(() => {
    if (!googleClientId || !googleButtonRef.current) return undefined;

    const script = document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.defer = true;
    script.onload = () => {
      if (!window.google?.accounts?.id || !googleButtonRef.current) return;
      window.google.accounts.id.initialize({
        client_id: googleClientId,
        callback: (response) => oauthActionRef.current?.('google', response.credential)
      });
      window.google.accounts.id.renderButton(googleButtonRef.current, {
        theme: 'filled_black',
        size: 'large',
        shape: 'rectangular',
        text: 'continue_with',
        width: Math.max(220, googleButtonRef.current.clientWidth)
      });
    };
    document.head.appendChild(script);
    return () => script.remove();
  }, [googleClientId]);

  const handleSignIn = async (event) => {
    event.preventDefault();
    setSubmitting(true);
    setStatus('');
    try {
      const response = await fetch(apiUrl('/v1/dev/token'), { method: 'POST' });
      if (!response.ok) throw new Error('The demo sign-in request failed.');
      const data = await response.json();
      if (!data.access_token) throw new Error('The server did not return a session token.');
      onSignIn(data.access_token, rememberMe);
    } catch (error) {
      setStatus(error.message || 'Unable to reach the demo backend.');
      setSubmitting(false);
    }
  };

  const showProviderSetup = (provider) => {
    const required = provider === 'Google'
      ? 'NEXT_PUBLIC_GOOGLE_CLIENT_ID in frontend/.env.local and GOOGLE_CLIENT_ID in backend/.env'
      : 'NEXT_PUBLIC_MICROSOFT_CLIENT_ID and NEXT_PUBLIC_MICROSOFT_TENANT_ID in frontend/.env.local, plus MICROSOFT_CLIENT_ID and MICROSOFT_TENANT_ID in backend/.env';
    setStatus(`${provider} OAuth setup pending. Add ${required}.`);
  };

  const signInWithMicrosoft = async () => {
    if (!microsoftClientId || !microsoftTenantId) {
      showProviderSetup('Microsoft');
      return;
    }

    setOauthBusy(true);
    setStatus('Opening Microsoft sign-in...');
    try {
      const msal = new PublicClientApplication({
        auth: {
          clientId: microsoftClientId,
          authority: `https://login.microsoftonline.com/${microsoftTenantId}`,
          redirectUri: process.env.NEXT_PUBLIC_MICROSOFT_REDIRECT_URI || window.location.origin
        },
        cache: { cacheLocation: 'sessionStorage' }
      });
      await msal.initialize();
      const result = await msal.loginPopup({ scopes: ['openid', 'profile', 'email'] });
      if (!result.idToken) throw new Error('Microsoft did not return an ID token.');
      await exchangeProviderToken('microsoft', result.idToken);
    } catch (error) {
      setStatus(error.message || 'Unable to complete Microsoft sign-in.');
      setOauthBusy(false);
    }
  };

  return (
    <div className="login-page">
      <header className="login-header">
        <div className="brand-wrap" aria-label="DevOnboard AI brand"><div className="brand-mark" aria-hidden="true"><span className="brand-mark-core" /></div><span className="brand-name">DevOnboard AI</span></div>
        <nav className="login-nav" aria-label="Main navigation"><a href="#">Features</a><a href="#">Pricing</a><a href="#">About</a></nav>
        <button type="button" className="nav-cta" onClick={() => document.querySelector('.login-card')?.scrollIntoView({ behavior: 'smooth' })}>Get Started</button>
      </header>
      <main className="login-main">
        <section className="login-hero">
          <h1>Build <span>Together</span>,<br />Go <span>Further.</span></h1>
          <p>A modern platform to understand your codebase, map architecture from real repository signals, and onboard teams with grounded context instead of guesswork.</p>
          <div className="feature-grid">
            <article className="feature-item"><div className="feature-icon purple"><Users size={18} /></div><div><h3>Collaborate</h3><p>Work together with a shared view of repo structure and ownership.</p></div></article>
            <article className="feature-item"><div className="feature-icon blue"><Sparkles size={18} /></div><div><h3>Trace features</h3><p>Connect product intent to the code paths that actually implement it.</p></div></article>
            <article className="feature-item"><div className="feature-icon teal"><ShieldCheck size={18} /></div><div><h3>Onboard faster</h3><p>Grounded architecture helps new contributors find their way.</p></div></article>
          </div>
          <div className="abstract-scene" aria-hidden="true"><div className="orb orb-one" /><div className="orb orb-two" /><div className="cube"><span className="cube-face front" /><span className="cube-face side" /><span className="cube-face top" /></div></div>
        </section>
        <section className="login-card-shell">
          <div className="login-card">
            <h2>Welcome <span>Back</span></h2>
            <p className="card-subtitle">Sign in to continue to DevOnboard AI</p>
            <form className="login-form" onSubmit={handleSignIn}>
              <label className="field"><span className="field-icon"><Mail size={18} /></span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="Email address" aria-label="Email address" autoComplete="username" required /></label>
              <label className="field"><span className="field-icon"><Lock size={18} /></span><input type={showPassword ? 'text' : 'password'} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Password" aria-label="Password" autoComplete="current-password" required /><button type="button" className="toggle-visibility" onClick={() => setShowPassword((value) => !value)} aria-label="Toggle password visibility">{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}</button></label>
              <div className="form-options"><label className="checkbox-row"><input type="checkbox" checked={rememberMe} onChange={(event) => setRememberMe(event.target.checked)} /><span>Remember me</span></label><a href="#" className="text-link">Forgot password?</a></div>
              <button type="submit" className="primary-action" disabled={submitting}><span>{submitting ? 'Signing In...' : 'Sign In'}</span><span className="action-arrow"><ArrowRight size={18} /></span></button>
              {status && <p className="status-line" role="status">{status}</p>}
            </form>
            <p className="signup-line">Don&apos;t have an account? <a href="#" className="text-link">Create one</a></p>
          </div>
        </section>
      </main>
    </div>
  );
}

function Workspace({ session, onSignOut }) {
  const [url, setUrl] = useState('https://github.com/karpathy/micrograd');
  const [repoId, setRepoId] = useState('');
  const [status, setStatus] = useState('');
  const [pipelineStage, setPipelineStage] = useState('');
  const [repoSummary, setRepoSummary] = useState(null);
  const [focusedNode, setFocusedNode] = useState(null);
  const [activeTab, setActiveTab] = useState('graph');
  const [analyzing, setAnalyzing] = useState(false);
  const pipelineOrder = { cloning: 0, scanning: 1, building_graph: 2, ready: 3 };
  const activePipelineStage = pipelineStage.startsWith('parsing:') ? 'scanning' : ['persisting', 'chunking'].includes(pipelineStage) ? 'building_graph' : pipelineStage;
  const parseProgress = pipelineStage.match(/^parsing:(\d+)\/(\d+)$/);
  const analysisReady = Boolean(repoId && status.includes('ready'));

  const analyze = async (event) => {
    event.preventDefault();
    if (!url.trim() || analyzing) return;

    setAnalyzing(true);
    setRepoSummary(null);
    setStatus('Status: queued | Stage: cloning');
    setPipelineStage('cloning');
    try {
      const response = await fetch(apiUrl('/v1/repositories/analyze'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${session.accessToken}` },
        body: JSON.stringify({ repo_url: url.trim() })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Repository analysis could not be started.');
      setRepoId(data.job_id);
      pollStatus(data.job_id);
    } catch (error) {
      setStatus(error.message || 'Unable to start repository analysis.');
      setAnalyzing(false);
    }
  };

  const pollStatus = (id) => {
    const interval = setInterval(async () => {
      const response = await fetch(apiUrl(`/v1/repositories/${id}`), { headers: { Authorization: `Bearer ${session.accessToken}` } });
      const data = await response.json();
      setStatus(`Status: ${data.status} | Stage: ${data.pipeline_stage}`);
      setPipelineStage(data.pipeline_stage || data.status);
      if (data.status === 'ready') {
        setRepoSummary(data);
        setAnalyzing(false);
        window.setTimeout(() => document.getElementById('workspace-results')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
      }
      if (data.status === 'ready' || data.status === 'failed') {
        setAnalyzing(false);
        clearInterval(interval);
      }
    }, 2000);
  };

  const navigateToFeature = (feature) => {
    if (!analysisReady) return;
    if (feature === 'trace') setActiveTab('trace');
    if (feature === 'graph' || feature === 'assistant') setActiveTab('graph');
    window.setTimeout(() => document.getElementById(feature)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0);
  };

  return (
    <div className="app-shell home-app-shell">
      <header className="home-nav">
        <a className="home-brand" href="#home" aria-label="Devonboard AI home">
          <span className="home-brand-mark"><GitBranch size={19} /></span>
          <span>Devonboard AI</span>
        </a>
        <nav className="home-nav-links" aria-label="Home navigation">
          <a href="#home" className="active" aria-current="page">Home</a>
          <span aria-disabled="true" title="Feature pages are not part of this MVP">Features</span>
          {repoId
            ? <a href="#analysis-process">How it works</a>
            : <span aria-disabled="true" title="The pipeline tracker appears after analysis starts">How it works</span>}
          <span aria-disabled="true" title="Pricing page is not part of this MVP">Pricing</span>
          <span aria-disabled="true" title="About page is not part of this MVP">About</span>
        </nav>
        <div className="home-nav-actions">
          <button type="button" className="theme-static" disabled aria-label="Theme toggle unavailable"><Sun size={18} /></button>
          <button type="button" className="home-signout" onClick={onSignOut}><LogOut size={16} /> Sign Out</button>
        </div>
      </header>

      <main id="home" className="home-content">
        <section className="home-hero">
          <div className="home-hero-copy">
            <div className="home-badge"><Sparkles size={14} /> AI Powered Repository Analysis</div>
            <h1>Understand <span>Any</span><br />Codebase <span>Instantly</span></h1>
            <p>Paste a repository link. DevOnboard AI analyzes its files and symbols, maps the architecture, and helps you explore how the code works.</p>
            <form className="home-repo-form" onSubmit={analyze}>
              <label className="home-repo-field">
                <Link2 size={20} aria-hidden="true" />
                <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://github.com/username/repository" aria-label="Repository URL" />
              </label>
              <button type="submit" className="home-analyze-button" disabled={!url.trim() || analyzing}>
                <Sparkles size={17} /> {analyzing ? 'Analyzing...' : 'Analyze Repository'}
              </button>
            </form>
            <div className="example-repos">
              <span>Try an example:</span>
              {[
                ['karpathy/micrograd', 'https://github.com/karpathy/micrograd'],
                ['vercel/next.js', 'https://github.com/vercel/next.js'],
                ['tiangolo/fastapi', 'https://github.com/tiangolo/fastapi']
              ].map(([label, repoUrl]) => <button type="button" key={label} onClick={() => setUrl(repoUrl)}>{label}</button>)}
            </div>
            {repoId && <div className="home-analysis-status" role="status">{status}</div>}
          </div>

          <div className="home-visual" aria-hidden="true">
            <div className="visual-grid" />
            <div className="visual-orbit orbit-a" />
            <div className="visual-orbit orbit-b" />
            <div className="visual-core"><Network size={56} strokeWidth={1.3} /></div>
            <div className="visual-card card-analyze"><Search size={16} /><span>Analyze</span></div>
            <div className="visual-card card-understand"><Lightbulb size={16} /><span>Understand</span></div>
            <div className="visual-card card-visualize"><Network size={16} /><span>Visualize</span></div>
            <div className="visual-card card-explore"><MessageSquare size={16} /><span>Explore</span></div>
          </div>
        </section>

        <section className="home-features" aria-labelledby="home-features-title">
          <div className="section-heading">
            <span>Explore your repository</span>
            <h2 id="home-features-title">From source to understanding</h2>
          </div>
          <div className="home-feature-grid">
            {[
              { title: 'Architecture Diagram', description: 'Explore how analyzed files, modules, and symbols connect.', icon: Network, color: 'violet', target: 'feature-architecture' },
              { title: 'Feature Trace', description: 'Follow a feature through the symbols and files that implement it.', icon: GitBranch, color: 'blue', target: 'feature-trace' },
              { title: 'Repository Assistant', description: 'Ask grounded questions and inspect the evidence behind answers.', icon: MessageSquare, color: 'mint', target: 'feature-assistant' },
              { title: 'Analysis Pipeline', description: 'Track repository traversal, parsing, extraction, and graph building.', icon: Lightbulb, color: 'amber', target: 'analysis-process' }
            ].map(({ title, description, icon: Icon, color, target }) => <article className={`home-feature-card ${color}`} key={title}>
              <div className="home-feature-icon"><Icon size={20} /></div>
              <div className="home-feature-copy"><h3>{title}</h3><p>{description}</p></div>
              <button type="button" className="feature-arrow" onClick={() => navigateToFeature(target)} disabled={!analysisReady} aria-label={`Open ${title}`}><ArrowUpRight size={17} /></button>
              <span className="feature-accent" />
            </article>)}
          </div>
        </section>

        {repoId && <section id="analysis-process" className="home-pipeline" aria-label="Repository analysis pipeline">
          <div className="home-pipeline-heading"><div><span>Repository analysis</span><h2>{repoSummary ? `${repoSummary.name} is ready` : 'Building your repository map'}</h2></div><span className={`pipeline-state ${status.includes('failed') ? 'failed' : analysisReady ? 'ready' : 'running'}`}>{status.includes('failed') ? 'Failed' : analysisReady ? 'Ready' : 'In progress'}</span></div>
          <div className="home-pipeline-steps" aria-label={`Pipeline stage: ${pipelineStage || 'queued'}`}>
            {['cloning', 'scanning', 'building_graph', 'ready'].map((stage, index) => {
              const activeIndex = pipelineOrder[activePipelineStage] ?? -1;
              return <span className={index < activeIndex ? 'complete' : index === activeIndex ? 'current' : ''} key={stage}><i>{index + 1}</i>{stage === 'cloning' ? 'Clone' : stage === 'scanning' ? 'Traverse & Parse' : stage === 'building_graph' ? 'Extract' : 'Graph'}</span>;
            })}
            {parseProgress && <b className="pipeline-progress">Parsing files {parseProgress[1]}/{parseProgress[2]}</b>}
          </div>
          {status.includes('failed') && <p className="analysis-error">{status}</p>}
        </section>}

        {analysisReady && <section id="workspace-results" className="home-results" aria-label="Repository results">
          <div className="sidebar">
            <button id="feature-architecture" className={`tab-button ${activeTab === 'graph' ? 'active' : ''}`} onClick={() => setActiveTab('graph')}>Architecture</button>
            <button id="feature-trace" className={`tab-button ${activeTab === 'trace' ? 'active' : ''}`} onClick={() => setActiveTab('trace')}>Feature Trace</button>
            <div id="feature-assistant">{repoSummary && <RepoAssistant repoId={repoId} token={session.accessToken} repoName={repoSummary.name} focusedNode={focusedNode} llmConfigured={repoSummary.llm_configured} />}</div>
          </div>
          <div className="content-pane">
            <div id="analysis-summary" className="analysis-summary">
              <div><span className="eyebrow">Analysis complete</span><strong>{repoSummary?.file_count || 0} files analyzed</strong></div>
              <span>{Object.keys(repoSummary?.languages || {}).length} languages</span>
              <span>{repoSummary?.symbol_count || 0} symbols indexed</span>
              <div className="pipeline-steps" aria-label={`Pipeline stage: ${pipelineStage}`}>
                {['cloning', 'scanning', 'building_graph', 'ready'].map((stage, index) => <span className={pipelineStage === stage ? 'current' : pipelineStage === 'ready' || (pipelineStage === 'building_graph' && index < 2) || (pipelineStage === 'scanning' && index < 1) ? 'complete' : ''} key={stage}><i>{index + 1}</i>{stage === 'cloning' ? 'Clone' : stage === 'scanning' ? 'Traverse & Parse' : stage === 'building_graph' ? 'Extract' : 'Graph'}</span>)}
              </div>
            </div>
            {activeTab === 'graph' && <ArchitectureGraph repoId={repoId} token={session.accessToken} grouped={repoSummary?.architecture_mode === 'grouped'} onSelectionChange={setFocusedNode} />}
            {activeTab === 'trace' && <FeatureTrace repoId={repoId} token={session.accessToken} />}
          </div>
        </section>}
      </main>
    </div>
  );
}
