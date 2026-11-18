import { permanentRedirect } from "next/navigation";

export default function WelcomeAlias() {
  permanentRedirect("/welcome");
}
