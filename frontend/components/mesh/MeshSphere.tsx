type Props = {
  size?: number;
  className?: string;
  opacity?: number;
};

export default function MeshSphere({
  size = 180,
  className = "",
  opacity = 0.12,
}: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 180 180"
      fill="none"
      stroke="#111"
      className={className}
      style={{ opacity }}
      aria-hidden="true"
    >
      <circle cx="90" cy="90" r="80" strokeWidth="0.8" />
      <ellipse cx="90" cy="90" rx="40" ry="80" strokeWidth="0.6" />
      <ellipse cx="90" cy="90" rx="20" ry="80" strokeWidth="0.5" />
      <ellipse cx="90" cy="90" rx="60" ry="80" strokeWidth="0.5" />
      <ellipse cx="90" cy="90" rx="80" ry="30" strokeWidth="0.6" />
      <ellipse cx="90" cy="90" rx="80" ry="55" strokeWidth="0.4" />
      <ellipse cx="90" cy="90" rx="80" ry="10" strokeWidth="0.4" />
      <line x1="10" y1="90" x2="170" y2="90" strokeWidth="0.4" />
      <line x1="90" y1="10" x2="90" y2="170" strokeWidth="0.4" />
    </svg>
  );
}
