import { Link } from "react-router";
import { buttonClass, Logo } from "../components/ui";

export function NotFound({ message = "Cette page n'existe pas." }: { message?: string }) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col items-center justify-center gap-6 px-6 text-center">
      <Logo size="lg" />
      <p className="text-[17px] text-muted">{message}</p>
      <Link to="/" className={buttonClass("primary")}>
        Retour à l'accueil
      </Link>
    </main>
  );
}
