import { useState, type CSSProperties } from "react";
import {
  CodeBlock,
  CodeBlockHeader,
  CodeBlockTitle,
  CodeBlockActions,
  CodeBlockCopyButton,
} from "./components/ai-elements/code-block";

/** Shared offline source/payload viewer built from Vercel AI Elements. */
export function SourceBlock({
  code,
  language,
  title,
  className,
  style,
  "aria-label": label,
}: {
  code: string;
  language: "python" | "json";
  title: string;
  className?: string;
  style?: CSSProperties;
  "aria-label"?: string;
}) {
  const [copyStatus, setCopyStatus] = useState("");
  return (
    <CodeBlock
      code={code}
      language={language}
      showLineNumbers={language === "python"}
      className={className}
      style={style}
      aria-label={label}
    >
      <CodeBlockHeader>
        <CodeBlockTitle>
          <span className="text-xs truncate">{title}</span>
        </CodeBlockTitle>
        <CodeBlockActions>
          <span role="status" className="text-xs">
            {copyStatus}
          </span>
          <CodeBlockCopyButton
            aria-label={
              language === "python" ? "Copy source code" : "Copy JSON"
            }
            onCopy={() => setCopyStatus("Copied")}
            onError={() =>
              setCopyStatus("Copy unavailable; select the text to copy.")
            }
          />
        </CodeBlockActions>
      </CodeBlockHeader>
    </CodeBlock>
  );
}
