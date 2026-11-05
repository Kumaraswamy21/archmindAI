"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, GitBranch, LoaderCircle, ShieldCheck, Upload } from "lucide-react";
import { BackLink } from "@/components/app-shell";
import { ErrorState } from "@/components/states";
import { api, ApiError } from "@/lib/api/client";
import { validateProjectAlias, validateProjectName } from "@/lib/project-validation";

const MAX_ZIP_BYTES = 500 * 1024 * 1024;

export default function NewProjectPage() {
  const router = useRouter();
  const [aliases, setAliases] = useState<string[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [alias, setAlias] = useState("");
  const [mode, setMode] = useState<"upload" | "approved">("upload");
  const [archive, setArchive] = useState<File | null>(null);
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
    if (validateProjectName(name) || (mode === "approved" && validateProjectAlias(alias, aliases ?? [])) ||
        (mode === "upload" && (!archive || !archive.name.toLowerCase().endsWith(".zip") || archive.size > MAX_ZIP_BYTES))) return;
    setSaving(true);
    try {
      const project = mode === "upload"
        ? await api.uploadRepository(name.trim(), archive!)
        : await api.register({ name: name.trim(), source_alias: alias });
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
  const archiveError = submitted && mode === "upload"
    ? !archive ? "Choose a ZIP file." : !archive.name.toLowerCase().endsWith(".zip") ? "Choose a .zip file." : archive.size > MAX_ZIP_BYTES ? "Choose a ZIP file no larger than 500 MB." : null
    : null;

  return <div className="page-stack page-form"><BackLink href="/projects">Back to projects</BackLink><div className="page-heading"><div><div className="eyebrow with-rule">PROJECT SETUP</div><h1>Add a project</h1><p>Upload a repository ZIP or connect an approved source.</p></div></div>
    <form className="form-card" onSubmit={submit} noValidate>
      <div className="form-card-head"><GitBranch size={20} /><div><h2>Repository details</h2><p>Source files are stored on the backend for analysis.</p></div></div>
      <div className="field"><label htmlFor="source-mode">Source type</label><select id="source-mode" value={mode} onChange={event => { setMode(event.target.value as "upload" | "approved"); setSubmitted(false); setFormError(null); }}><option value="upload">Upload a ZIP file</option><option value="approved">Approved source</option></select></div>
      <div className="field"><label htmlFor="project-name">Display name</label><input id="project-name" value={name} onChange={event => { setName(event.target.value); setFormError(null); }} required maxLength={100} aria-invalid={!!nameError} aria-describedby={nameError ? "project-name-error" : "project-name-help"} placeholder="e.g. Customer Portal" autoComplete="off" /><small id="project-name-help">Choose a name your team will recognize.</small>{nameError && <span className="field-error" id="project-name-error" role="alert">{nameError}</span>}</div>
      {mode === "upload" ? <div className="field"><label htmlFor="repository-zip">Repository ZIP</label><input id="repository-zip" type="file" accept=".zip,application/zip" onChange={event => { setArchive(event.target.files?.[0] ?? null); setFormError(null); }} aria-invalid={!!archiveError} aria-describedby={archiveError ? "repository-zip-error" : "repository-zip-help"} /><small id="repository-zip-help">Up to 500 MB. Analysis supports .java, .py, and .ts source files.</small>{archiveError && <span className="field-error" id="repository-zip-error" role="alert">{archiveError}</span>}</div>
        : loadError ? <ErrorState message={loadError} onRetry={() => setRevision(value => value + 1)} /> : <div className="field"><label htmlFor="source-alias">Approved source</label><select id="source-alias" value={alias} onChange={event => { setAlias(event.target.value); setFormError(null); }} required aria-invalid={!!aliasError} aria-describedby={aliasError ? "source-alias-error" : "source-alias-help"}><option value="">{aliases === null ? "Loading approved sources" : aliases.length ? "Select an approved alias" : "No approved sources available"}</option>{aliases?.map(value => <option key={value} value={value}>{value}</option>)}</select><small id="source-alias-help">Only server configured aliases can be registered.</small>{aliasError && <span className="field-error" id="source-alias-error" role="alert">{aliasError}</span>}</div>}
      {formError && <div className="form-error" role="alert"><p>{formError}</p>{requestId && <small>Request ID: {requestId}</small>}</div>}
      <div className="form-actions"><span><ShieldCheck size={17} /> Read only analysis</span><button type="submit" className="button button-primary" disabled={saving}>{saving ? <><LoaderCircle size={17} className="spin" /> Creating project</> : <>{mode === "upload" && <Upload size={17} />} Create project <ArrowRight size={17} /></>}</button></div>
    </form>
  </div>;
}
