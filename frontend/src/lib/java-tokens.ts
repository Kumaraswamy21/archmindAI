export type JavaToken = { text: string; kind: "plain" | "keyword" | "type" | "number" | "string" | "comment" | "annotation" | "punctuation" };

const keywords = new Set([
  "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class", "const",
  "continue", "default", "do", "double", "else", "enum", "extends", "final", "finally", "float",
  "for", "goto", "if", "implements", "import", "instanceof", "int", "interface", "long", "native",
  "new", "package", "private", "protected", "public", "record", "return", "short", "static", "strictfp",
  "super", "switch", "synchronized", "this", "throw", "throws", "transient", "try", "var", "void",
  "volatile", "while", "yield", "sealed", "permits", "non", "null", "true", "false",
]);

export function tokenizeJava(content: string): JavaToken[][] {
  const lines = content.split(/\r?\n/);
  if (content.endsWith("\n")) lines.pop();
  let blockComment = false;
  let textBlock = false;
  return lines.map(line => {
    const tokens: JavaToken[] = [];
    const push = (kind: JavaToken["kind"], text: string) => {
      const previous = tokens.at(-1);
      if (previous?.kind === kind) previous.text += text;
      else tokens.push({ kind, text });
    };
    let index = 0;
    while (index < line.length) {
      const rest = line.slice(index);
      if (blockComment) {
        const end = rest.indexOf("*/");
        const length = end < 0 ? rest.length : end + 2;
        push("comment", rest.slice(0, length)); index += length;
        if (end >= 0) blockComment = false;
        continue;
      }
      if (textBlock) {
        const end = rest.indexOf('"""');
        const length = end < 0 ? rest.length : end + 3;
        push("string", rest.slice(0, length)); index += length;
        if (end >= 0) textBlock = false;
        continue;
      }
      if (rest.startsWith("//")) { push("comment", rest); break; }
      if (rest.startsWith("/*")) { blockComment = true; continue; }
      if (rest.startsWith('"""')) { textBlock = true; continue; }
      if (rest[0] === '"' || rest[0] === "'") {
        const quote = rest[0];
        let end = 1;
        while (end < rest.length) {
          if (rest[end] === "\\") { end += 2; continue; }
          if (rest[end++] === quote) break;
        }
        push("string", rest.slice(0, end)); index += end; continue;
      }
      const annotation = /^@[A-Za-z_$][\w$]*/.exec(rest);
      if (annotation) { push("annotation", annotation[0]); index += annotation[0].length; continue; }
      const number = /^(?:0[xX][0-9a-fA-F_]+|\d[\d_]*(?:\.[\d_]+)?(?:[eE][+-]?\d+)?[fFdDlL]?)/.exec(rest);
      if (number) { push("number", number[0]); index += number[0].length; continue; }
      const word = /^[A-Za-z_$][\w$]*/.exec(rest);
      if (word) {
        const kind = keywords.has(word[0]) ? "keyword" : /^[A-Z]/.test(word[0]) ? "type" : "plain";
        push(kind, word[0]); index += word[0].length; continue;
      }
      const kind = /[{}()[\];,.]/.test(rest[0]) ? "punctuation" : "plain";
      push(kind, rest[0]); index += 1;
    }
    return tokens;
  });
}
