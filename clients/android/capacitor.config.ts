import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.sciomenihilscire.skrt.android",
  appName: "sKrt",
  webDir: "../../frontend/dist",
  bundledWebRuntime: false,
  server: {
    androidScheme: "https"
  }
};

export default config;
