import React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { useSignOut } from '@/hooks/auth/useSignOut';
import Link from 'next/link';

export function SignOut(props) {
  const { asChild = false, children, ...rest } = props;
  const Comp = asChild ? Slot : Link;

  const { signOutUrl } = useSignOut();

  return (
    <Comp {...rest} href={signOutUrl}>
      {children}
    </Comp>
  );
}
