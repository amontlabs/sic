import { useEffect, useRef, useState } from "react";
import { downloadBlob, renderShareCard, shareFileName, shareOrDownload, type ShareCardInput } from "../lib/shareCard";
import { verdictLabel } from "../lib/verdict";
import { IconClose, IconDownload, IconShare, IconSpinner } from "./icons";
import { Button } from "./ui";

export function ShareDialog({ input, onClose }: { input: ShareCardInput; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const filename = shareFileName(input);
  const canShareFiles =
    typeof navigator !== "undefined" &&
    typeof navigator.canShare === "function" &&
    blob !== null &&
    navigator.canShare({ files: [new File([blob], filename, { type: "image/png" })] });

  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);

  useEffect(() => {
    let revoked = false;
    let objectUrl: string | null = null;
    renderShareCard(input)
      .then((b) => {
        if (revoked) return;
        objectUrl = URL.createObjectURL(b);
        setBlob(b);
        setUrl(objectUrl);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Erreur de rendu"));
    return () => {
      revoked = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // input is rebuilt by the parent on each render: render once per dialog opening
  }, []);

  const text = `${input.speakerName} : « ${input.quote} » — ${verdictLabel(input.label)}. ${input.headline} (sic.vote)`;

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) ref.current?.close();
      }}
      aria-labelledby="share-title"
      className="m-auto w-[min(92vw,440px)] rounded-2xl border border-line bg-surface p-0 text-ink shadow-2xl backdrop:bg-black/50 backdrop:backdrop-blur-[2px]"
      data-testid="share-dialog"
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 id="share-title" className="text-[15px] font-semibold">
          Partager cette vérification
        </h2>
        <button
          type="button"
          onClick={() => ref.current?.close()}
          className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-ink"
          aria-label="Fermer"
        >
          <IconClose className="size-5" />
        </button>
      </div>
      <div className="p-4">
        <div className="mx-auto aspect-[4/5] w-full max-w-[300px] overflow-hidden rounded-lg border border-line bg-surface-2">
          {url ? (
            <img src={url} alt={`Carte de partage : ${text}`} className="h-full w-full object-contain" data-testid="share-preview" />
          ) : error ? (
            <p className="p-6 text-sm text-muted">{error}</p>
          ) : (
            <div className="flex h-full items-center justify-center text-muted">
              <IconSpinner className="size-6" />
              <span className="sr-only">Création de l'image…</span>
            </div>
          )}
        </div>
        <p className="mt-3 text-center text-[12.5px] text-subtle">Image 1080 × 1350 générée sur votre appareil.</p>
        <div className="mt-4 flex justify-center gap-2">
          {canShareFiles && (
            <Button
              variant="primary"
              disabled={!blob}
              onClick={async () => {
                if (!blob) return;
                const r = await shareOrDownload(blob, filename, text);
                if (r === "downloaded") setNote("Image téléchargée.");
              }}
            >
              <IconShare className="size-4" />
              Partager
            </Button>
          )}
          <Button
            variant={canShareFiles ? "secondary" : "primary"}
            disabled={!blob}
            onClick={() => {
              if (!blob) return;
              downloadBlob(blob, filename);
              setNote("Image téléchargée.");
            }}
            data-testid="share-download"
          >
            <IconDownload className="size-4" />
            Télécharger
          </Button>
        </div>
        <p className="mt-2 min-h-5 text-center text-[12.5px] text-muted" role="status">
          {note}
        </p>
      </div>
    </dialog>
  );
}
