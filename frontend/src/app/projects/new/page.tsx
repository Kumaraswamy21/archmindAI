"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, GitBranch, LoaderCircle, ShieldCheck } from "lucide-react";
import { BackLink } from "@/components/app-shell";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { api, ApiError } from "@/lib/api/client";
import { validateProjectAlias, validateProjectName } from "@/lib/project-validation";

export default function NewProjectPage() {
  const router = useRouter();
  const [aliases, setAliases] = useState<string[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [alias, setAlias] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api.approvedAliases(controller.signal).then(value => { setAliases(value.aliases); setLoadError(null); }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setLoadError(cause instanceof ApiError ? cause.message : "Approved sources could not be loaded.");
    });
    return () => controller.abort();
  }, [revision]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitted(true);
    setFormError(null);
    setRequestId(null);
    if (validateProjectName(name) || validateProjectAlias(alias, aliases ?? [])) return;
    setSaving(true);
    try {
      const project = await api.register({ name: name.trim(), source_alias: alias });
      router.push(`/projects/${project.repository_id}`);
    } catch (cause) {
      if (cause instanceof ApiError) {
        setFormError(cause.code === "REPOSITORY_ALREADY_REGISTERED"
          ? "This project name or approved source is already registered. Choose another or open the existing project."
          : cause.message);
        setRequestId(cause.requestId ?? null);
      } else {
        setFormError("Project registration failed.");
      }
      setSaving(false);
    }
  }

  const nameError = submitted ? validateProjectName(name) : null;
  const aliasError = submitted ? validateProjectAlias(alias, aliases ?? []) : null;

  return <div className="page-stack page-form"><BackLink href="/projects">Back to projects</BackLink><div className="page-heading"><div><div className="eyebrow with-rule">PROJECT SETUP</div><h1>Add a project</h1><p>Connect a repository already approved by your backend administrator.</p></div></div>
    {loadError ? <ErrorState message={loadError} onRetry={() => setRevision(value => value + 1)} /> : aliases === null ? <LoadingState label="Loading approved sources" /> : aliases.length === 0 ? <EmptyState title="No approved sources available" description="Configure an approved repository alias on the backend, then return here to add it." action={false} /> : <form className="form-card" onSubmit={submit} noValidate>
      <div className="form-card-head"><GitBranch size={20} /><div><h2>Repository details</h2><p>The source location stays on the backend.</p></div></div>
      <div className="field"><label htmlFor="project-name">Display name</label><input id="project-name" value={name} onChange={event => { setName(event.target.value); setFormError(null); }} required maxLength={100} aria-invalid={!!nameError} aria-describedby={nameError ? "project-name-error" : "project-name-help"} placeholder="e.g. Customer Portal" autoComplete="off" /><small id="project-name-help">Choose a name your team will recognize.</small>{nameError && <span className="field-error" id="project-name-error" role="alert">{nameError}</span>}</div>
      <div className="field"><label htmlFor="source-alias">Approved source</label><select id="source-alias" value={alias} onChange={event => { setAlias(event.target.value); setFormError(null); }} required aria-invalid={!!aliasError} aria-describedby={aliasError ? "source-alias-error" : "source-alias-help"}><option value="">Select an approved alias</option>{aliases.map(value => <option key={value} value={value}>{value}</option>)}</select><small id="source-alias-help">Only server configured aliases can be registered.</small>{aliasError && <span className="field-error" id="source-alias-error" role="alert">{aliasError}</span>}</div>
      {formError && <div className="form-error" role="alert"><p>{formError}</p>{requestId && <small>Request ID: {requestId}</small>}</div>}
      <div className="form-actions"><span><ShieldCheck size={17} /> Read only source access</span><button type="submit" className="button button-primary" disabled={saving}>{saving ? <><LoaderCircle size={17} className="spin" /> Creating project</> : <>Create project <ArrowRight size={17} /></>}</button></div>
    </form>}
  </div>;
}
