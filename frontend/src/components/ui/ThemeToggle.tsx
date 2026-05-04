import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "../../contexts/ThemeContext";
import type { ThemeMode } from "../../contexts/ThemeContext";

interface Option {
  value: ThemeMode;
  label: string;
  Icon: typeof Sun;
}

const OPTIONS: Option[] = [
  { value: "light", label: "Light theme", Icon: Sun },
  { value: "system", label: "System theme", Icon: Monitor },
  { value: "dark", label: "Dark theme", Icon: Moon },
];

export function ThemeToggle() {
  const { mode, setMode } = useTheme();

  return (
    <div
      role="group"
      aria-label="Theme"
      className="inline-flex items-center rounded-md border border-border bg-surface p-0.5"
    >
      {OPTIONS.map(({ value, label, Icon }) => {
        const active = mode === value;
        return (
          <button
            key={value}
            type="button"
            aria-label={label}
            aria-pressed={active}
            onClick={() => setMode(value)}
            className={
              "flex h-7 w-7 items-center justify-center rounded " +
              (active
                ? "bg-surface-muted text-text"
                : "text-text-muted hover:text-text")
            }
          >
            <Icon size={14} aria-hidden />
          </button>
        );
      })}
    </div>
  );
}
