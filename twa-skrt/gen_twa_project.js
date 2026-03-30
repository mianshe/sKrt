const path = require("path");

async function main() {
  const bubblewrapCliRoot = path.join(
    process.env.APPDATA || "",
    "npm",
    "node_modules",
    "@bubblewrap",
    "cli"
  );
  // Bubblewrap CLI installs @bubblewrap/core as a dependency.
  const core = require(path.join(
    bubblewrapCliRoot,
    "node_modules",
    "@bubblewrap",
    "core"
  ));
  const shared = require(path.join(bubblewrapCliRoot, "dist", "lib", "cmds", "shared.js"));

  const targetDir = path.resolve(__dirname, "skrt-twa");
  const manifestFile = path.join(targetDir, "twa-manifest.json");

  // Base TWA manifest from local webmanifest URL.
  const twa = await core.TwaManifest.fromWebManifest(
    "http://127.0.0.1:8787/local-manifest.webmanifest"
  );

  twa.host = "sciomenihilscire.com";
  twa.startUrl = "/";
  twa.name = "sKrt";
  twa.launcherName = "sKrt";
  twa.packageId = "com.skrt.app";
  twa.appVersionCode = 1;
  twa.appVersionName = "1.0.0";
  twa.display = "standalone";
  twa.orientation = "portrait";
  // themeColor/backgroundColor already come from the Web Manifest.

  // Use local-served icon so init is deterministic.
  twa.iconUrl = "http://127.0.0.1:8787/icon.png";
  twa.maskableIconUrl = undefined;
  twa.monochromeIconUrl = undefined;

  // Use our pre-generated release keystore.
  twa.signingKey.path = path.join(targetDir, "android-keystore.jks");
  twa.signingKey.alias = "skrt";

  await twa.saveToFile(manifestFile);

  const prompt = { printMessage: () => {} };
  const ok = await shared.updateProject(true, null, prompt, targetDir, manifestFile);
  if (!ok) process.exit(1);
  process.stdout.write("ok\n");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

