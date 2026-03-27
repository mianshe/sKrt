/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: "#4f46e5",
        cross: "#f43f5e",
      },
    },
  },
  plugins: [],
};
