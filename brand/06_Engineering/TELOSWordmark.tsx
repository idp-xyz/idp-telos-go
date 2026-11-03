import type { CSSProperties } from 'react';

/** Assets are copied unchanged from the final IDP TELOS brand package into /brand/. */
type LogoProps = {
  variant?: 'primary' | 'light' | 'binary-static' | 'binary-animated';
  height?: number;
  className?: string;
};
const srcs = {
  primary: '/brand/TELOS_Primary_Locked_Transparent.svg',
  light: '/brand/TELOS_Primary_Locked_Navy_Transparent.svg',
  'binary-static': '/brand/TELOS_BinaryE_Static_Cropped_Transparent.svg',
  'binary-animated': '/brand/TELOS_BinaryE_Animated_Cropped_Transparent.svg',
} as const;

export function TELOSWordmark({variant = 'primary', height = 44, className}: LogoProps) {
  // Prefer a static logo for accessible reduced-motion environments.
  // Inline CSS animations in external SVG may not pause everywhere; swap asset instead.
  return <picture className={className}>
    {variant === 'binary-animated' ? <source media="(prefers-reduced-motion: reduce)" srcSet={srcs['binary-static']} /> : null}
    <img src={srcs[variant]} alt="TELOS" height={height}
      style={{width:'auto',maxWidth:'100%',display:'block'} as CSSProperties}/>
  </picture>;
}
