"use client";
import { useEffect, useRef, useState } from 'react';
import { Pause, Play, RotateCcw, SkipBack, SkipForward } from 'lucide-react';
import { apiUrl } from '../lib/api';

export default function FeatureTrace({ repoId, token }) {
  const [query, setQuery] = useState('');
  const [steps, setSteps] = useState([]);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [traceMode, setTraceMode] = useState('relevance');
  const [traceMessage, setTraceMessage] = useState('');
  const [visibleStepCount, setVisibleStepCount] = useState(5);
  const [currentStep, setCurrentStep] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const playbackRef = useRef(null);

  useEffect(() => () => clearInterval(playbackRef.current), []);

  useEffect(() => {
    if (!playing || currentStep >= steps.length - 1) {
      if (currentStep >= steps.length - 1) setPlaying(false);
      return undefined;
    }
    playbackRef.current = setInterval(() => setCurrentStep(step => step + 1), 850);
    return () => clearInterval(playbackRef.current);
  }, [playing, currentStep, steps.length]);

  const startTrace = async () => {
    setSteps([]);
    setCurrentStep(-1);
    setPlaying(false);
    setLoading(true);
    setDone(false);
    setTraceMode('relevance');
    setTraceMessage('');
    setVisibleStepCount(5);

    try {
      const response = await fetch(apiUrl(`/v1/repositories/${repoId}/trace`), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({ query })
      });

      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        
        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');
        
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = JSON.parse(line.slice(6));
            if (data.type === 'done') {
              setDone(true);
              setLoading(false);
            } else if (data.type === 'trace_info') {
              setTraceMode(data.mode || 'relevance');
              setTraceMessage(data.message || '');
            } else {
              setSteps(prev => {
                const next = [...prev, data];
                setCurrentStep(current => current < 0 ? 0 : current);
                return next;
              });
            }
          }
        }
      }
    } catch (err) {
      console.error("Trace failed", err);
      setLoading(false);
    }
  };

  return (
    <div className="trace-panel">
      <h2>Feature Trace</h2>
      <p className="trace-intro">Follow the evidence-backed path through the analyzed code.</p>
      <div className="trace-query">
        <input 
          value={query} 
          onChange={e => setQuery(e.target.value)} 
          placeholder="e.g. how does backward propagate gradients"
        />
        <button className="button button-primary" onClick={startTrace} disabled={loading || !query}>Trace</button>
      </div>

      {traceMessage && <div className={`trace-mode ${traceMode}`}>
        <strong>{traceMode === 'causal' ? 'Relationship-backed path' : 'Related code sections'}</strong>
        <span>{traceMessage}</span>
      </div>}

      {steps.length > 0 && traceMode === 'causal' && <div className="playback" aria-label="Trace playback controls">
        <button className="button button-quiet" onClick={() => setCurrentStep(0)} title="Replay from beginning"><RotateCcw size={15} /></button>
        <button className="button button-quiet" onClick={() => setCurrentStep(step => Math.max(0, step - 1))} title="Step back"><SkipBack size={15} /></button>
        <button className="button button-quiet" onClick={() => setPlaying(value => !value)} title={playing ? 'Pause trace' : 'Play trace'}>
          {playing ? <Pause size={15} /> : <Play size={15} />}
        </button>
        <button className="button button-quiet" onClick={() => setCurrentStep(step => Math.min(steps.length - 1, step + 1))} title="Step forward"><SkipForward size={15} /></button>
        <span className="step-count">Step {Math.max(currentStep + 1, 0)} / {steps.length}</span>
      </div>}
      {steps.length > 0 && traceMode === 'relevance' && <div className="step-count trace-related-count">Related sections: {steps.length}</div>}

      <div className="trace-list">
        {steps.slice(0, visibleStepCount).map((step, i) => (
          <div className={`trace-step ${i === currentStep ? 'current' : ''} ${step.type === 'confidence_halt' ? 'halted' : ''}`} key={i}>
            <div className="trace-symbol">
              {traceMode === 'causal' ? `Step ${step.step}` : `Related ${step.step}`}: {step.symbol}
            </div>
            {step.transition && <div className="trace-transition">-&gt; {step.transition.type} from {step.transition.from}</div>}
            {step.file && (
              <div className="trace-file">
                {step.file}:{step.line_start}-{step.line_end}
              </div>
            )}
            {step.message && (
              <div className="trace-message">{step.message}</div>
            )}
            <div className="trace-confidence">
              {traceMode === 'causal' ? 'Path confidence' : 'Relevance'}: {(step.confidence * 100).toFixed(0)}%
            </div>
          </div>
        ))}
        {steps.length > visibleStepCount && <button className="button button-quiet trace-more" onClick={() => setVisibleStepCount(count => count + 5)}>Show more ({steps.length - visibleStepCount} remaining)</button>}
        {loading && <div className="trace-empty">Tracing call path...</div>}
        {done && <div className="trace-confidence">Trace complete</div>}
      </div>
    </div>
  );
}
