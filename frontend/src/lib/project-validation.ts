export function validateProjectName(value: string): string | null {
  const name = value.trim();
  if (!name) return "Enter a project name.";
  if (Array.from(name).length > 100) return "Use 100 characters or fewer.";
  if (/[\u0000-\u001F\u007F-\u009F]/u.test(name)) return "Use printable characters only.";
  return null;
}

export function validateProjectAlias(value: string, approved: string[]): string | null {
  if (!value) return "Choose an approved source.";
  if (!approved.includes(value)) return "Choose a source from the approved list.";
  return null;
}
