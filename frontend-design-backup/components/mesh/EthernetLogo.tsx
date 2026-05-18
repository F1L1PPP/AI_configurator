type Props = { size?: number; className?: string; strokeWidth?: number };

export default function EthernetLogo({
  size = 28,
  className = "",
  strokeWidth = 1,
}: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-label="Cisco AI Config Ethernet logo"
    >
      <line x1="16" y1="2" x2="16" y2="18" />
      <line x1="22" y1="0" x2="22" y2="18" />
      <line x1="28" y1="2" x2="28" y2="18" />
      <line x1="34" y1="0" x2="34" y2="18" />
      <line x1="40" y1="2" x2="40" y2="18" />
      <line x1="46" y1="0" x2="46" y2="18" />

      <path d="M10 18 L54 18 L52 36 L12 36 Z" />
      <path d="M24 18 L24 14 L40 14 L40 18" />

      <line x1="15" y1="20" x2="15" y2="34" strokeWidth={strokeWidth * 0.75} />
      <line x1="20" y1="20" x2="20" y2="34" strokeWidth={strokeWidth * 0.75} />
      <line x1="25" y1="20" x2="25" y2="34" strokeWidth={strokeWidth * 0.75} />
      <line x1="30" y1="20" x2="30" y2="34" strokeWidth={strokeWidth * 0.75} />
      <line x1="34" y1="20" x2="34" y2="34" strokeWidth={strokeWidth * 0.75} />
      <line x1="39" y1="20" x2="39" y2="34" strokeWidth={strokeWidth * 0.75} />
      <line x1="44" y1="20" x2="44" y2="34" strokeWidth={strokeWidth * 0.75} />
      <line x1="49" y1="20" x2="49" y2="34" strokeWidth={strokeWidth * 0.75} />

      <line x1="12" y1="26" x2="52" y2="26" strokeWidth={strokeWidth * 0.5} opacity="0.5" />

      <rect x="22" y="36" width="20" height="6" />
      <line x1="32" y1="42" x2="32" y2="62" strokeWidth={strokeWidth * 0.75} />
      <line x1="28" y1="46" x2="28" y2="62" strokeWidth={strokeWidth * 0.6} opacity="0.7" />
      <line x1="36" y1="46" x2="36" y2="62" strokeWidth={strokeWidth * 0.6} opacity="0.7" />
    </svg>
  );
}
