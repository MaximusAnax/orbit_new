/**
 * VoiceKin — a person's voice, reproducible only while a living consent record says so.
 *
 * The hard part of this product is not synthesis; it is consent enforcement and
 * provenance. So the screen leads with the standing question — may this house
 * speak in anyone's voice right now? — and answers it in semantic colour rather
 * than branding, then names every refusal the gate can issue, in the order the
 * gate evaluates them, read live from the API's own schema.
 *
 * Nothing is enrolled here, and that is the honest state: enrolment needs
 * recorded audio and the automation surface accepts no uploads by design
 * (SCOPE decision 16). The empty state therefore teaches what enrolment costs
 * instead of inventing a voice nobody consented to.
 */

import { useState } from "react";
import type { ReactNode } from "react";
import { client, type ApiError } from "../lib/api";
import { useMutation, useQuery } from "../lib/hooks";
import {
  Async, Button, ErrorNote, Facts, Field, Input, Meter, Page, Panel, Pill, Select,
  Stat, StatRow, Table, TextArea,
} from "../ui/kit";
import type { Column } from "../ui/kit";
import "./voicekin.css";

const api = client("voicekin");

/* -------------------------------------------------------------------- types */

type ConsentStatus = "draft" | "verified" | "rejected" | "revoked";
type Tone = "neutral" | "ok" | "warn" | "bad";

type Health = { status: string; initialized: boolean; operator_name: string | null };

type Profile = {
  id: string;
  display_name: string;
  relationship: string;
  status: "active" | "purged";
  enabled: boolean;
  embedder_id: string | null;
  enrollment_fingerprint: string | null;
  enrolled_at: string | null;
  awaiting_consent: boolean;
  consent_status: ConsentStatus | null;
  created_at: string;
  updated_at: string;
};

type Consent = {
  id: string;
  profile_id: string;
  draft_index: number;
  status: ConsentStatus;
  scope_contexts: string[];
  expires_at: string | null;
  nonce: string;
  statement_text: string;
  audio_sha256: string | null;
  similarity: number | null;
  threshold: number | null;
  embedder_id: string | null;
  enrollment_fingerprint: string | null;
  reject_reason: string | null;
  drafted_at: string;
  decided_at: string | null;
  revoked_at: string | null;
  revocation_reason: string | null;
};

type Utterance = {
  id: string;
  profile_id: string;
  attempt_index: number;
  consent_id: string | null;
  text: string;
  text_raw: string;
  context: string;
  status: "refused" | "rendered";
  refusal_reason: string | null;
  synth_id: string | null;
  seed: number | null;
  output_sha256: string | null;
  duration_s: number | null;
  requested_at: string;
};

type AuditRecord = {
  seq: number;
  ts: string;
  event: string;
  profile_id: string | null;
  consent_id: string | null;
  utterance_id: string | null;
  detail: Record<string, unknown>;
  prev_hash: string;
  record_hash: string;
};

type ChainState = {
  ok: boolean;
  head_seq: number;
  head_hash: string;
  bad_seq?: number | null;
  problem?: string | null;
};

type Provenance = {
  utterance: Utterance;
  profile_id: string;
  profile_display_name: string;
  audit_records: AuditRecord[];
};

/** Just enough of the OpenAPI document to read the gate's own vocabulary. */
type SchemaDoc = { components?: { schemas?: Record<string, { enum?: string[] }> } };

/* ------------------------------------------------------------- vocabulary */

/** The gate's refusal reasons carry no prose in the schema; this is the prose. */
const REFUSAL: Record<string, { title: string; tone: Tone; detail: string }> = {
  profile_purged: {
    title: "The voice was erased",
    tone: "bad",
    detail:
      "Audio, embeddings, centroid and voice parameters are gone. Only hashes remain, because the audit chain must still verify.",
  },
  profile_disabled: {
    title: "Synthesis switched off",
    tone: "warn",
    detail: "The operator paused this profile. Consent is untouched — this is the reversible one.",
  },
  no_enrollment: {
    title: "There is no voice to speak with",
    tone: "neutral",
    detail: "The profile has no enrolment fingerprint: no accepted samples, no centroid, nothing to render from.",
  },
  no_consent: {
    title: "Nobody has consented",
    tone: "warn",
    detail:
      "No consent record, or only an unread draft. Drafting a statement is not consent; the statement has to be read aloud and recorded.",
  },
  consent_rejected: {
    title: "The consent recording failed verification",
    tone: "bad",
    detail: "Wrong speaker, unusable audio, or replayed enrolment audio. A rejected take never authorises anything.",
  },
  consent_revoked: {
    title: "Consent was withdrawn",
    tone: "bad",
    detail:
      "Checked again on every delivery, not just every render — so clips already rendered stop playing too.",
  },
  consent_expired: {
    title: "Past the expiry the owner set",
    tone: "warn",
    detail: "The boundary refuses: at the expiry instant the voice is already silent. The record itself is untouched.",
  },
  enrollment_changed: {
    title: "The enrolment moved under the consent",
    tone: "warn",
    detail:
      "Consent is bound to the exact sample set it was given for. Add or remove a sample and the fingerprint changes, so every prior consent stops authorising until the owner grants again.",
  },
  scope_mismatch: {
    title: "Outside what was consented to",
    tone: "warn",
    detail: "Consent for reminders does not authorise an alarm. The scope is a list of contexts, and it is enforced.",
  },
};

/** Why a consent recording is refused, in the order FR-5(b) checks. */
const REJECTION: Record<string, { title: string; detail: string }> = {
  not_enrolled: {
    title: "Nothing left to match against",
    detail: "Samples were removed after the statement was drafted, so the profile is no longer enrolled-complete.",
  },
  audio_quality: {
    title: "The recording failed screening",
    detail:
      "Too short or too long, clipped, too little voiced speech, or too noisy. A bad take is a bad take, not a borderline yes.",
  },
  reused_enrollment_audio: {
    title: "That was a replay, not a grant",
    detail: "The file was byte-identical to an enrolment sample. Re-submitting someone's own audio is not them agreeing.",
  },
  speaker_mismatch: {
    title: "Someone else read the statement",
    detail:
      "Similarity to the enrolment centroid fell below the committed threshold. A housemate cannot consent on another person's behalf.",
  },
};

const FALLBACK_REFUSALS = Object.keys(REFUSAL);
const FALLBACK_REJECTIONS = Object.keys(REJECTION);
const FALLBACK_CONTEXTS = ["announcement", "reminder", "alarm", "doorbell", "timer", "status"];

const CONSENT_TONE: Record<ConsentStatus, Tone> = {
  draft: "warn",
  verified: "ok",
  rejected: "bad",
  revoked: "bad",
};

/* ---------------------------------------------------------------- helpers */

function enumOf(doc: SchemaDoc, name: string, fallback: string[]): string[] {
  const values = doc.components?.schemas?.[name]?.enum;
  return Array.isArray(values) && values.length > 0 ? values : fallback;
}

function shortHash(hash: string | null | undefined, n = 12): string {
  return hash ? `${hash.slice(0, n)}…` : "—";
}

function when(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

const DAY_MS = 86_400_000;

/** Verified consent is only green while it is also unexpired and not expiring soon. */
function consentTone(consent: Consent): Tone {
  if (consent.revoked_at) return "bad";
  if (consent.status !== "verified") return CONSENT_TONE[consent.status];
  if (!consent.expires_at) return "ok";
  const left = new Date(consent.expires_at).getTime() - Date.now();
  if (Number.isNaN(left)) return "ok";
  if (left <= 0) return "bad";
  return left < 14 * DAY_MS ? "warn" : "ok";
}

function consentLabel(consent: Consent): string {
  if (consent.revoked_at) return "revoked";
  if (consent.status === "verified" && consent.expires_at) {
    const left = new Date(consent.expires_at).getTime() - Date.now();
    if (!Number.isNaN(left)) {
      if (left <= 0) return "expired";
      const days = Math.ceil(left / DAY_MS);
      if (days < 14) return `expires in ${days}d`;
    }
  }
  return consent.status;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** A 403 from this API carries the gate's reason; surface it rather than "forbidden". */
function refusalOf(error: ApiError | undefined): string | null {
  if (!error || error.status !== 403) return null;
  const top = asRecord(error.detail);
  const nested = top ? asRecord(top.detail) : null;
  const source = nested && "reason" in nested ? nested : top;
  const reason = source?.reason;
  return typeof reason === "string" ? reason : null;
}

const SHA256_RE = /^[0-9a-f]{64}$/;

/** From the README's worked example — rendered on a different instance, so tracing
 *  it here should and does come back unknown_output. That is the point of it. */
const EXAMPLE_HASH = "e0b4272a949eef3a99fb726075c8b788970716ccb8464826447e1c3ec5f18bdb";

/* ------------------------------------------------------------- components */

function ProfileConsentPill({ profile }: { profile: Profile }) {
  if (profile.status === "purged") return <Pill tone="bad">purged</Pill>;
  if (!profile.consent_status) return <Pill tone="warn">never consented</Pill>;
  return <Pill tone={CONSENT_TONE[profile.consent_status]}>{profile.consent_status}</Pill>;
}

/** The one sentence the operator must not be able to misread. */
function Standing({ profiles }: { profiles: Profile[] }) {
  const usable = profiles.filter(
    (p) => p.status === "active" && p.enabled && p.consent_status === "verified",
  );
  const blocked = profiles.filter(
    (p) => p.status === "purged" || p.consent_status === "revoked" || p.consent_status === "rejected",
  );

  let tone: Tone = "neutral";
  let title = "No voice is enrolled on this instance";
  let detail =
    "There is nothing to speak with and nothing to consent to. Every synthesis request is refused before it reaches a synthesiser.";

  if (usable.length > 0) {
    tone = "ok";
    title = `${usable.length} ${usable.length === 1 ? "voice" : "voices"} may be used right now`;
    detail = `${usable.map((p) => p.display_name).join(", ")} — each governed by a verified, unrevoked consent record, re-checked on every render and every replay.`;
  } else if (profiles.length > 0) {
    tone = blocked.length > 0 ? "bad" : "warn";
    title = "No voice may be used right now";
    detail =
      "Profiles exist, but none is governed by a consent record that currently authorises anything. The gate refuses; there is no override.";
  }

  return (
    <div className={`vk-standing vk-tone-${tone}`} role="status">
      <span className="vk-standing-mark" aria-hidden="true" />
      <div className="vk-standing-text">
        <p className="vk-standing-title">{title}</p>
        <p className="vk-standing-detail">{detail}</p>
      </div>
    </div>
  );
}

/** The empty state is the demo's primary screen: what enrolment actually costs. */
function EnrolmentLadder({ operator }: { operator: string }) {
  const steps = [
    {
      title: "Create the profile",
      command: 'voicekin profile add partner --name "Amina" --relationship partner',
      body: "A name and a relationship. No audio, no biometrics, nothing that can speak yet.",
      gate: "Gate: no_enrollment",
    },
    {
      title: "Add three or more clean samples",
      command: "voicekin enroll partner s1.wav s2.wav s3.wav",
      body:
        "Each WAV is screened before it counts: 3–30 s long, under 0.5 % clipped, at least 40 % voiced, SNR ≥ 15 dB, and ≥ 10 s of voiced audio across the set. A leave-one-out coherence check refuses a set that mixes two different speakers.",
      gate: "Refusal: incoherent_enrollment · thresholds committed in data/calibration.json",
    },
    {
      title: "Draft the statement the owner must read",
      command: "voicekin consent draft partner --scope announcement,reminder",
      body:
        "Prints the exact sentence, naming the owner, the operator, the contexts consented to, the expiry, and a one-time verification code. Drafting is not consent.",
      gate: "Gate: no_consent — a draft authorises nothing",
    },
    {
      title: "The owner reads it aloud; you record it",
      command: "voicekin consent grant partner consent.wav",
      body:
        "The recording is screened, checked against every enrolment sample for byte-identical replay, then matched to the enrolment centroid by speaker embedding. Only a similarity at or above the committed threshold verifies. A rejected take is hashed and discarded — it never leaves audio behind.",
      gate: "Rejections: audio_quality · reused_enrollment_audio · speaker_mismatch",
    },
    {
      title: "Only now will the house speak",
      command: 'voicekin say partner "Dinner is ready" --context announcement',
      body:
        "Every render and every replay re-runs the same gate against current state. The rendered WAV's PCM payload hash goes into the utterance row and the audit chain, so any bit-exact copy can be traced back here.",
      gate: "Authorised — and audited either way",
    },
  ];

  return (
    <div className="vk-ladder-wrap">
      <p className="vk-lead">
        Nothing can be enrolled from this screen. The automation surface accepts no audio upload by
        design — enrolment and consent recording are administrative operations that need the voice
        owner physically present, so they live on the CLI. This is what has to happen, in order,
        before this instance can speak in anyone's voice.
      </p>

      <ol className="vk-ladder">
        {steps.map((s, i) => (
          <li className="vk-step" key={s.title}>
            <span className="vk-step-n num" aria-hidden="true">
              {i + 1}
            </span>
            <div className="vk-step-body">
              <p className="vk-step-title">{s.title}</p>
              <code className="vk-step-cmd mono">{s.command}</code>
              <p className="vk-step-detail">{s.body}</p>
              <p className="vk-step-gate mono">{s.gate}</p>
            </div>
          </li>
        ))}
      </ol>

      <figure className="vk-statement">
        <figcaption className="vk-statement-cap">
          The committed consent statement — rendered per profile at draft time
        </figcaption>
        <blockquote>
          I, <span className="vk-slot">owner’s name</span>, consent to the VoiceKin system operated
          by <span className="vk-slot">{operator}</span> reproducing my voice for:{" "}
          <span className="vk-slot">contexts</span>. This consent{" "}
          <span className="vk-slot">expiry clause</span> and I may revoke it at any time, effective
          immediately. Verification code: <span className="vk-slot">nonce</span>. Date:{" "}
          <span className="vk-slot">date</span>.
        </blockquote>
      </figure>

      <p className="vk-note vk-note-warn">
        Biometrics precede consent, and the product says so. Embeddings exist from the moment
        samples are accepted — before any consent record exists. A profile left enrolled without a
        verified consent past the grace period is flagged <code className="mono">awaiting_consent</code>{" "}
        rather than deleted: destroying a voice owner's data on a timer is its own hazard.
      </p>
    </div>
  );
}

function ConsentCard({ consent, onRevoked }: { consent: Consent; onRevoked: () => void }) {
  const [reason, setReason] = useState("");
  const revoke = useMutation((why: string) =>
    api.post<Consent>(`/consents/${consent.id}/revoke`, { reason: why || null }),
  );
  const governing = consent.status === "verified" && !consent.revoked_at;

  return (
    <article className={`vk-consent vk-tone-${consentTone(consent)}`}>
      <header className="vk-consent-head">
        <Pill tone={consentTone(consent)}>{consentLabel(consent)}</Pill>
        <code className="mono vk-consent-id">{consent.id.slice(0, 16)}</code>
        <span className="vk-consent-scope">
          {consent.scope_contexts.length ? consent.scope_contexts.join(" · ") : "no contexts"}
        </span>
      </header>

      {consent.similarity != null && consent.threshold != null ? (
        <Meter
          value={consent.similarity}
          max={1}
          tone={consent.similarity >= consent.threshold ? "ok" : "bad"}
          label={
            <>
              <span>speaker match</span>
              <span className="num">
                {consent.similarity.toFixed(3)} vs threshold {consent.threshold.toFixed(3)}
              </span>
            </>
          }
        />
      ) : null}

      <Facts
        items={[
          ["Drafted", when(consent.drafted_at)],
          ["Decided", when(consent.decided_at)],
          ["Expires", consent.expires_at ? when(consent.expires_at) : "no expiry"],
          ["Embedder", <code className="mono">{consent.embedder_id ?? "—"}</code>],
          [
            "Bound to enrolment",
            <code className="mono">{shortHash(consent.enrollment_fingerprint)}</code>,
          ],
          ["Verification code", <code className="mono">{consent.nonce}</code>],
          ...(consent.reject_reason
            ? ([
                [
                  "Rejected because",
                  <span className="vk-bad">
                    {REJECTION[consent.reject_reason]?.title ?? consent.reject_reason}
                  </span>,
                ],
              ] as [ReactNode, ReactNode][])
            : []),
          ...(consent.revoked_at
            ? ([
                [
                  "Revoked",
                  <span className="vk-bad">
                    {when(consent.revoked_at)}
                    {consent.revocation_reason ? ` — ${consent.revocation_reason}` : ""}
                  </span>,
                ],
              ] as [ReactNode, ReactNode][])
            : []),
        ]}
      />

      <p className="vk-statement-text">{consent.statement_text}</p>

      {governing ? (
        <form
          className="vk-revoke"
          onSubmit={(e) => {
            e.preventDefault();
            void revoke.run(reason).then(onRevoked);
          }}
        >
          <Field label="Reason (optional)" hint="Withdrawal must be as easy as granting — one call, no ceremony.">
            <Input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="why the voice owner is withdrawing"
            />
          </Field>
          <Button type="submit" variant="danger" pending={revoke.pending}>
            Revoke this consent
          </Button>
        </form>
      ) : null}
      <ErrorNote error={revoke.error} />
    </article>
  );
}

function SynthesisAttempt({
  profile,
  contexts,
  scope,
}: {
  profile: Profile;
  contexts: string[];
  scope: string[];
}) {
  const [text, setText] = useState("Dinner is ready");
  const [context, setContext] = useState(contexts[0] ?? "announcement");
  const say = useMutation((body: { text: string; context: string }) =>
    api.post<Utterance>("/synthesize", {
      profile_id: profile.id,
      text: body.text,
      context: body.context,
      seed: 7,
    }),
  );
  const refused = refusalOf(say.error);
  const rendered = say.data;

  return (
    <div className="vk-say">
      <form
        className="vk-say-form"
        onSubmit={(e) => {
          e.preventDefault();
          void say.run({ text, context });
        }}
      >
        <Field label="What the house should say">
          <TextArea value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={500} />
        </Field>
        <Field
          label="Context"
          hint={
            scope.length
              ? `Consented scope: ${scope.join(", ")}. Anything else refuses with scope_mismatch.`
              : "No consented scope — every context refuses."
          }
        >
          <Select value={context} onChange={(e) => setContext(e.target.value)}>
            {contexts.map((c) => (
              <option key={c} value={c}>
                {c}
                {scope.includes(c) ? " — in scope" : " — outside scope, will refuse"}
              </option>
            ))}
          </Select>
        </Field>
        <Button type="submit" variant="primary" pending={say.pending} disabled={!text.trim()}>
          Attempt synthesis
        </Button>
      </form>

      {refused ? (
        <div className="vk-refusal vk-tone-bad" role="alert">
          <div className="vk-refusal-head">
            <Pill tone="bad">refused</Pill>
            <code className="mono">{refused}</code>
          </div>
          <p className="vk-refusal-title">{REFUSAL[refused]?.title ?? "The gate refused"}</p>
          <p className="vk-refusal-detail">
            {REFUSAL[refused]?.detail ??
              "The authorization gate declined this request and persisted the refusal."}
          </p>
          <p className="vk-note">
            The refusal is not just an error code: it is stored as an utterance row with status{" "}
            <code className="mono">refused</code> and appended to the audit chain. Nothing was
            rendered.
          </p>
        </div>
      ) : null}

      {say.error && !refused ? <ErrorNote error={say.error} /> : null}

      {rendered ? (
        <div className="vk-rendered vk-tone-ok">
          <div className="vk-refusal-head">
            <Pill tone={rendered.status === "rendered" ? "ok" : "bad"}>{rendered.status}</Pill>
            <code className="mono">{rendered.id.slice(0, 16)}</code>
          </div>
          <Facts
            items={[
              ["Under consent", <code className="mono">{shortHash(rendered.consent_id, 16)}</code>],
              ["Normalised text", <span className="vk-quiet">{rendered.text}</span>],
              ["Context", rendered.context],
              [
                "Duration",
                <span className="num">{rendered.duration_s != null ? `${rendered.duration_s.toFixed(2)} s` : "—"}</span>,
              ],
              ["Synthesiser", <code className="mono">{rendered.synth_id ?? "—"}</code>],
              ["Seed", <span className="num">{rendered.seed ?? "—"}</span>],
              ["Payload sha256", <code className="mono vk-hash">{rendered.output_sha256 ?? "—"}</code>],
            ]}
          />
          {rendered.status === "rendered" ? (
            <audio
              className="vk-audio"
              controls
              src={`/api/voicekin/utterances/${rendered.id}/audio`}
            >
              Your browser cannot play this clip.
            </audio>
          ) : null}
          <p className="vk-note">
            That hash is the provenance handle: paste it into the trace panel below and this
            instance will name the voice, the consent record and the audit records behind it.
            Delivering the clip re-runs the gate, so a revocation stops replays too.
          </p>
        </div>
      ) : null}
    </div>
  );
}

function ProfileDetail({
  profile,
  contexts,
  onClose,
  onChanged,
}: {
  profile: Profile;
  contexts: string[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const consents = useQuery(() => api.get<Consent[]>(`/profiles/${profile.id}/consents`), [profile.id]);

  return (
    <Panel
      title={`${profile.display_name} — consent history`}
      hint="The record with the greatest (drafted_at, draft_index) governs. Older records are never consulted."
      actions={<Button onClick={onClose}>Close</Button>}
    >
      <Async
        query={consents}
        emptyWhen={(list) => list.length === 0}
        empty={{
          title: "No consent record exists for this voice",
          detail: "Enrolment happened; nobody has read the statement aloud. The gate refuses with no_consent.",
        }}
      >
        {(list) => {
          const governing = [...list].sort((a, b) =>
            a.drafted_at === b.drafted_at
              ? b.draft_index - a.draft_index
              : a.drafted_at < b.drafted_at
                ? 1
                : -1,
          )[0];
          const authorises =
            governing &&
            governing.status === "verified" &&
            !governing.revoked_at &&
            governing.enrollment_fingerprint === profile.enrollment_fingerprint;

          return (
            <>
              {governing && !authorises ? (
                <p className="vk-note vk-note-bad">
                  The governing record does not authorise anything right now. Reverting an
                  enrolment does not resurrect a superseded consent — the newer record still
                  governs.
                </p>
              ) : null}
              <div className="vk-consents">
                {list.map((c) => (
                  <ConsentCard
                    key={c.id}
                    consent={c}
                    onRevoked={() => {
                      consents.reload();
                      onChanged();
                    }}
                  />
                ))}
              </div>
              <SynthesisAttempt
                profile={profile}
                contexts={contexts}
                scope={authorises ? governing.scope_contexts : []}
              />
            </>
          );
        }}
      </Async>
    </Panel>
  );
}

function TracePanel() {
  const [hash, setHash] = useState("");
  const trace = useMutation((sha: string) => api.post<Provenance>("/verify-output", { output_sha256: sha }));
  const valid = SHA256_RE.test(hash.trim());
  const unknown = trace.error?.status === 404;

  return (
    <Panel
      title="Trace a rendered clip"
      hint="Every rendered WAV is attributable by the sha256 of its PCM payload — the data chunk only, so a metadata rewrite does not break the lookup."
    >
      <form
        className="vk-trace"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) void trace.run(hash.trim());
        }}
      >
        <Field
          label="Payload sha256"
          hint="64 lowercase hex characters. Anything else is rejected before it reaches the store."
        >
          <Input
            value={hash}
            onChange={(e) => setHash(e.target.value)}
            placeholder={EXAMPLE_HASH}
            spellCheck={false}
            aria-invalid={hash.length > 0 && !valid}
          />
        </Field>
        <div className="vk-trace-actions">
          <Button type="submit" variant="primary" pending={trace.pending} disabled={!valid}>
            Trace this clip
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setHash(EXAMPLE_HASH);
              trace.reset();
            }}
          >
            Use the README's example hash
          </Button>
        </div>
      </form>

      {unknown ? (
        <div className="vk-note vk-note-warn" role="status">
          <strong>No match on this instance.</strong> Nothing here rendered that audio, so there is
          no voice, consent record or audit trail to attach to it. VoiceKin answers{" "}
          <code className="mono">unknown_output</code> rather than guessing — an attribution it
          cannot prove would be worth less than none.
        </div>
      ) : null}
      {trace.error && !unknown ? <ErrorNote error={trace.error} /> : null}

      {trace.data ? (
        <div className="vk-tone-ok vk-provenance">
          <Facts
            items={[
              ["Voice", `${trace.data.profile_display_name} (${trace.data.profile_id})`],
              ["Utterance", <code className="mono">{trace.data.utterance.id}</code>],
              ["Said", <span className="vk-quiet">{trace.data.utterance.text_raw}</span>],
              ["Context", trace.data.utterance.context],
              ["Under consent", <code className="mono">{shortHash(trace.data.utterance.consent_id, 16)}</code>],
              ["Rendered", when(trace.data.utterance.requested_at)],
              ["Audit records", <span className="num">{trace.data.audit_records.length}</span>],
            ]}
          />
          <ul className="vk-audit-inline">
            {trace.data.audit_records.map((r) => (
              <li key={r.seq}>
                <span className="num">#{r.seq}</span> <code className="mono">{r.event}</code>{" "}
                <span className="vk-quiet">{when(r.ts)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="vk-note">
        Honest limit: attribution is bit-exact. It survives a copy and a metadata rewrite, not a
        re-encode, resample or trim. Robust watermarking is named as a later upgrade, not claimed
        here. Every delivered WAV also gets a sidecar JSON manifest — utterance, profile, consent,
        payload hash, audit sequence — so a delivered copy describes itself.
      </p>
    </Panel>
  );
}

function AuditPanel() {
  const chain = useQuery(() => api.get<ChainState>("/audit/verify"), []);
  const records = useQuery(() => api.get<AuditRecord[]>("/audit", { since_seq: 0 }), []);

  const columns: Column<AuditRecord>[] = [
    { key: "seq", header: "Seq", numeric: true, width: "4.5rem", render: (r) => r.seq },
    { key: "ts", header: "When", render: (r) => <span className="vk-quiet">{when(r.ts)}</span> },
    { key: "event", header: "Event", render: (r) => <code className="mono">{r.event}</code> },
    {
      key: "subject",
      header: "Subject",
      render: (r) => (
        <code className="mono">{shortHash(r.profile_id ?? r.consent_id ?? r.utterance_id, 10)}</code>
      ),
    },
    {
      key: "hash",
      header: "Record hash",
      render: (r) => <code className="mono">{shortHash(r.record_hash, 10)}</code>,
    },
  ];

  return (
    <Panel
      title="Audit chain"
      hint="Every profile, consent, synthesis, refusal and delivery is appended and hash-chained. Detail stores hashes, never audio, so a purge never breaks verification."
    >
      <Async query={chain}>
        {(c) => (
          <>
            <Facts
              items={[
                [
                  "Chain",
                  c.ok ? (
                    <Pill tone="ok">verifies</Pill>
                  ) : (
                    <Pill tone="bad">broken at seq {c.bad_seq ?? "?"}</Pill>
                  ),
                ],
                ["Head sequence", <span className="num">{c.head_seq}</span>],
                ["Head hash", <code className="mono vk-hash">{c.head_hash}</code>],
                ...(c.problem
                  ? ([["Problem", <span className="vk-bad">{c.problem}</span>]] as [ReactNode, ReactNode][])
                  : []),
              ]}
            />
            <p className="vk-note vk-note-warn">
              Anchor that head hash somewhere VoiceKin cannot touch. A linear unkeyed chain detects
              edits, deletions and reordering — it cannot detect an attacker with database write
              access who truncates the tail and re-chains correctly. That anchor is the whole
              mitigation, and the product says so rather than implying more.
            </p>
          </>
        )}
      </Async>

      <Async
        query={records}
        emptyWhen={(list) => list.length === 0}
        empty={{
          title: "Genesis — nothing has happened yet",
          detail:
            "No profile created, no sample accepted or rejected, no consent drafted, verified or revoked, nothing rendered and nothing refused. The chain starts from sha256(\"voicekin-genesis\").",
        }}
      >
        {(list) => (
          <Table
            columns={columns}
            rows={list.slice(-50).reverse()}
            rowKey={(r) => String(r.seq)}
            caption="Audit records, newest first"
          />
        )}
      </Async>
    </Panel>
  );
}

/* ------------------------------------------------------------------- page */

export default function VoiceKin() {
  const [selected, setSelected] = useState<string | null>(null);
  const health = useQuery(() => api.get<Health>("/health"), []);
  const profiles = useQuery(() => api.get<Profile[]>("/profiles"), []);
  const schema = useQuery(() => api.get<SchemaDoc>("/openapi.json"), []);

  const operator = health.data?.operator_name ?? "the operator";
  const contexts = enumOf(schema.data ?? {}, "Context", FALLBACK_CONTEXTS);
  const chosen = profiles.data?.find((p) => p.id === selected) ?? null;

  const profileColumns: Column<Profile>[] = [
    {
      key: "voice",
      header: "Voice",
      render: (p) => (
        <span className="vk-voice">
          <span className="vk-voice-name">{p.display_name}</span>
          <span className="vk-quiet">{p.relationship}</span>
        </span>
      ),
    },
    { key: "consent", header: "Consent", render: (p) => <ProfileConsentPill profile={p} /> },
    {
      key: "flag",
      header: "Flag",
      render: (p) => (p.awaiting_consent ? <Pill tone="warn">awaiting consent</Pill> : "—"),
    },
    {
      key: "enrolment",
      header: "Enrolment",
      render: (p) =>
        p.enrollment_fingerprint ? (
          <code className="mono">{shortHash(p.enrollment_fingerprint, 10)}</code>
        ) : (
          <span className="vk-warn">not enrolled</span>
        ),
    },
    {
      key: "synthesis",
      header: "Synthesis",
      render: (p) => (p.enabled ? "enabled" : <Pill tone="warn">disabled</Pill>),
    },
    { key: "since", header: "Enrolled", render: (p) => <span className="vk-quiet">{when(p.enrolled_at)}</span> },
  ];

  return (
    <Page
      title="VoiceKin"
      lede="Preserve a person's voice with their recorded consent — and make the consent enforceable. Synthesis is impossible without a verified consent record bound to the exact enrolment; revocation kills the voice instantly, including replays of audio already rendered."
      actions={
        <Button
          onClick={() => {
            health.reload();
            profiles.reload();
          }}
        >
          Refresh state
        </Button>
      }
    >
      <Panel title="Can this house speak?" hint="The question the product exists to answer honestly.">
        <Async query={profiles}>
          {(list) => (
            <>
              <Standing profiles={list} />
              <StatRow>
                <Stat label="Voice profiles" value={list.length} />
                <Stat
                  label="Usable now"
                  value={
                    list.filter((p) => p.status === "active" && p.enabled && p.consent_status === "verified")
                      .length
                  }
                  sub="verified consent, enabled"
                  tone={
                    list.some((p) => p.status === "active" && p.enabled && p.consent_status === "verified")
                      ? "ok"
                      : undefined
                  }
                />
                <Stat
                  label="Awaiting consent"
                  value={list.filter((p) => p.awaiting_consent).length}
                  sub="enrolled, never granted"
                  tone={list.some((p) => p.awaiting_consent) ? "warn" : undefined}
                />
                <Stat
                  label="Blocked"
                  value={
                    list.filter(
                      (p) =>
                        p.status === "purged" ||
                        p.consent_status === "revoked" ||
                        p.consent_status === "rejected",
                    ).length
                  }
                  sub="revoked, rejected or purged"
                  tone={
                    list.some(
                      (p) =>
                        p.status === "purged" ||
                        p.consent_status === "revoked" ||
                        p.consent_status === "rejected",
                    )
                      ? "bad"
                      : undefined
                  }
                />
              </StatRow>
            </>
          )}
        </Async>

        <Async query={health}>
          {(h) => (
            <Facts
              items={[
                ["Operator", h.operator_name ?? "unnamed"],
                [
                  "Instance",
                  h.initialized ? <Pill tone="ok">initialised</Pill> : <Pill tone="bad">not initialised</Pill>,
                ],
                [
                  "Enrolment surface",
                  "CLI only — this API accepts no audio upload, by design",
                ],
              ]}
            />
          )}
        </Async>
      </Panel>

      <Panel
        title="Voices on this instance"
        hint="A voice may be used only while a verified, unrevoked consent record, bound to the current enrolment, governs it."
      >
        <Async query={profiles}>
          {(list) =>
            list.length === 0 ? (
              <EnrolmentLadder operator={operator} />
            ) : (
              <Table
                columns={profileColumns}
                rows={list}
                rowKey={(p) => p.id}
                onRowClick={(p) => setSelected(p.id === selected ? null : p.id)}
                selectedKey={selected ?? undefined}
                caption="Enrolled voice profiles"
              />
            )
          }
        </Async>
      </Panel>

      {chosen ? (
        <ProfileDetail
          profile={chosen}
          contexts={contexts}
          onClose={() => setSelected(null)}
          onChanged={profiles.reload}
        />
      ) : null}

      <Panel
        title="What the gate refuses"
        hint="One authorization function mediates every synthesis and every delivery. These are its refusal reasons, in evaluation order, read live from the API's own schema."
      >
        <Async query={schema}>
          {(doc) => (
            <ol className="vk-reasons">
              {enumOf(doc, "RefusalReason", FALLBACK_REFUSALS).map((reason, i) => {
                const copy = REFUSAL[reason];
                return (
                  <li className={`vk-reason vk-tone-${copy?.tone ?? "neutral"}`} key={reason}>
                    <span className="vk-reason-n num" aria-hidden="true">
                      {i + 1}
                    </span>
                    <div className="vk-reason-body">
                      <div className="vk-reason-head">
                        <code className="mono">{reason}</code>
                        <span className="vk-reason-title">{copy?.title ?? "Refused"}</span>
                      </div>
                      <p className="vk-reason-detail">
                        {copy?.detail ?? "The gate declined; the refusal is persisted and audited."}
                      </p>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </Async>
        <p className="vk-note">
          A refusal is a first-class outcome, not an exception: it is written as an utterance row
          with status <code className="mono">refused</code> and appended to the audit chain before
          the caller hears about it. Delivery re-authorises against current state, so revoking
          consent silences clips that were rendered while it was still valid.
        </p>
      </Panel>

      <Panel
        title="Why a consent recording is rejected"
        hint="Checked in this order when the owner's recording is attached. A rejected recording is hashed and discarded — a failed or impostor take never leaves audio behind."
      >
        <Async query={schema}>
          {(doc) => (
            <div className="vk-rejections">
              {enumOf(doc, "ConsentRejectReason", FALLBACK_REJECTIONS).map((reason) => {
                const copy = REJECTION[reason];
                return (
                  <div className="vk-rejection" key={reason}>
                    <code className="mono">{reason}</code>
                    <p className="vk-reason-title">{copy?.title ?? "Rejected"}</p>
                    <p className="vk-reason-detail">
                      {copy?.detail ?? "The recording did not satisfy the verification rule."}
                    </p>
                  </div>
                );
              })}
            </div>
          )}
        </Async>
        <p className="vk-note vk-note-warn">
          Residual risk, stated plainly: a deepfake of the owner's voice reading the statement could
          pass this speaker match. Anti-spoofing is a named later adapter, not a claim this build
          makes.
        </p>
      </Panel>

      <TracePanel />
      <AuditPanel />
    </Page>
  );
}
