import React, { useEffect, useRef, useState } from 'react';
import { request } from '../api.js';
import { getAccess, api } from '../session.js';
import { T } from '../i18n.js';
import { scrubDetail } from '../status.js';
import { captureVoiceWav } from '../voice-capture.js';
import { Card, Spinner, ErrorNote, Empty } from '../ui.jsx';

/* Voice: mic → backend STT → same chat_respond as typed chat (RBAC + RAG +
   citations) → TTS playback. Everything runs server-side; if the mic is
   denied or a provider is down we say so honestly and keep typed chat on
   the Assistant page working. */

export default function Voice({ user, lang }) {
  const [st, setSt] = useState(null);
  const [stErr, setStErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState(''); // 'recording' | 'transcribing' | 'answering'
  const [err, setErr] = useState('');
  const [rec, setRec] = useState(null);
  const [result, setResult] = useState(null); // voice/chat response
  const [audio, setAudio] = useState(null);
  const audioRef = useRef(null);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    request('/api/v1/voice/status').then(setSt)
      .catch((e) => { setStErr(e.message); });
  }, []);
  useEffect(() => () => { try { audioRef.current?.pause(); } catch { /* noop */ } }, []);

  const start = async () => {
    setErr(''); setResult(null); setAudio(null);
    let rec;
    try {
      rec = await captureVoiceWav(); // user gesture -> mic permission prompt
    } catch (e) {
      setErr(e.message);
      return;
    }
    setRec(rec); setPhase('recording');
  };
  const stop = async () => {
    if (phase !== 'recording' || !rec) return; // transcribing/answering runs to completion
    setRec(null);
    setPhase('transcribing'); setBusy(true);
    try {
      const blob = await rec.stop(); // real 16kHz WAV (or honest empty-recording error)
      const fd = new FormData();
      fd.append('f', blob, 'voice.wav');
      fd.append('lang', lang);
      fd.append('mode', 'general');
      setPhase('answering');
      const j = await request('/api/v1/voice/chat', { method: 'POST', body: fd });
      setResult(j);
      if (j.audio_base64) {
        setAudio('data:' + (j.audio_mime || 'audio/wav') + ';base64,' + j.audio_base64);
      } else if (j.audio_error) {
        setErr('Transcript and answer are ready, but ' + scrubDetail(j.audio_error).toLowerCase());
      }
    } catch (e) {
      setErr(e.message);
    } finally { setBusy(false); setPhase(''); }
  };

  const play = () => {
    if (!audio) return;
    try {
      const a = new Audio(audio);
      audioRef.current = a; setPlaying(true);
      a.onended = () => setPlaying(false);
      a.play().catch(() => { setPlaying(false); setErr('Playback was blocked by the browser.'); });
    } catch { setPlaying(false); }
  };
  const stopAudio = () => { try { audioRef.current?.pause(); } catch { /* noop */ } setPlaying(false); };

  const providersOk = st && st.stt?.available && st.tts?.available;
  const answer = result?.message?.content || '';
  const sources = result?.sources || [];

  return (
    <div>
      <div className="page-head">
        <h2>{T(lang, 'voiceAssistant')}</h2>
        <p className="mut">{T(lang, 'voiceHint2')}</p>
      </div>

      <div className="grid-2">
        <Card title="Voice status">
          {stErr && <ErrorNote error={stErr} />}
          {!st && !stErr && <Spinner label={T(lang, 'loading')} />}
          {st && (
            <div className="chip-row">
              <span className={'chip ' + (st.stt.available ? 'chip-green' : 'chip-red')}>
                Voice input • {st.stt.available ? 'Ready' : 'Unavailable'}
              </span>
              <span className={'chip ' + (st.tts.available ? 'chip-green' : 'chip-red')}>
                Voice playback • {st.tts.available ? 'Ready' : 'Unavailable'}
              </span>
              <span className="chip">languages: {st.languages?.length || 0}</span>
            </div>
          )}
          {st && !providersOk && (
            <div className="note note-warn">
              {T(lang, 'voiceInDown')} {T(lang, 'voiceOutDown')} Answers stay readable as text.
            </div>
          )}
          {providersOk && <div className="note note-green">Voice Assistant • Ready. Your language: <code>{lang}</code></div>}
        </Card>

        <Card title="Ask by voice">
          <div className="row" style={{ justifyContent: 'center', padding: '10px 0' }}>
            {!phase
              ? <button className="mic-btn" onClick={start} title="Start recording">🎙</button>
              : <button className="mic-btn rec" onClick={stop} title="Stop recording">⏹</button>}
          </div>
          <p className="mut" style={{ textAlign: 'center' }}>
            {phase === 'recording' && <span className="bad">● recording… press stop when done</span>}
            {phase === 'transcribing' && 'Transcribing on the server…'}
            {phase === 'answering' && 'Getting answer…'}
            {!phase && 'Press the mic and ask.'}
          </p>
          <ErrorNote error={err} />
        </Card>
      </div>

      {result && (
        <Card title={T(lang, 'transcript')}>
          <div className="msg-user">{result.transcript}
            {typeof result.stt_confidence === 'number' && (
              <div className="msg-who">confidence {result.stt_confidence.toFixed(2)}</div>)}
          </div>
          <div className="msg-who" style={{ marginTop: 10 }}>{T(lang, 'answer')} · {result.elapsed_s}s</div>
          <div className="msg-ai" style={{ whiteSpace: 'pre-wrap' }}>
            {sources.length ? answer.split('\n\nSources:\n')[0] : answer}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            {audio && !playing && <button className="btn btn-mini" onClick={play}>▶ {T(lang, 'playback')}</button>}
            {audio && playing && <button className="btn btn-ghost btn-mini" onClick={stopAudio}>{T(lang, 'stop')}</button>}
            {!audio && result.audio_error && <span className="mut">audio omitted: {result.audio_error}</span>}
            {!audio && !result.audio_error && <span className="mut">no audio returned</span>}
          </div>
          <h4 className="mut" style={{ fontSize: 13, margin: '12px 0 6px' }}>
            {T(lang, 'sources')} ({sources.length})
          </h4>
          {sources.length === 0
            ? <p className="mut">{T(lang, 'noSources')}</p>
            : sources.map((src, i) => (
              <div className="cite" key={i}>
                <b>{src.filename}</b> {src.page ? `· page ${src.page}` : ''} · chunk {src.chunk_index} · score {src.score?.toFixed?.(3) ?? src.score}
                <div className="meta">doc {src.doc_id}</div>
              </div>
            ))}
        </Card>
      )}
    </div>
  );
}
