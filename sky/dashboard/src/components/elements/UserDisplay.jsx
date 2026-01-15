import React from 'react';
import Link from 'next/link';
import PropTypes from 'prop-types';
import { getUserLink, isServiceAccount } from '@/utils/userUtils';

/**
 * ServiceAccountBadge component - A reusable badge for service accounts
 */
const ServiceAccountBadge = () => (
  <span className="px-2 py-0.5 text-xs bg-blue-100 text-blue-700 rounded font-medium ml-1">
    SA
  </span>
);

/**
 * UserDisplay component - Displays a user with clickable link and SA badge if applicable
 *
 * @param {Object} props
 * @param {string} props.username - The display name of the user
 * @param {string} props.userHash - The user hash (used for routing and SA detection)
 * @param {string} [props.className] - Additional CSS classes for the container
 * @param {string} [props.linkClassName] - Additional CSS classes for the link
 * @param {boolean} [props.showBadge=true] - Whether to show the SA badge for service accounts
 */
export const UserDisplay = ({
  username,
  userHash,
  className = 'flex items-center gap-1',
  linkClassName = 'text-gray-700 hover:text-blue-600 hover:underline',
  showBadge = true,
}) => {
  const isServiceAcc = isServiceAccount(userHash);
  const userUrl = getUserLink(userHash);

  return (
    <div className={className}>
      <Link href={userUrl} className={linkClassName}>
        {username}
      </Link>
      {showBadge && isServiceAcc && <ServiceAccountBadge />}
    </div>
  );
};

UserDisplay.propTypes = {
  username: PropTypes.string.isRequired,
  userHash: PropTypes.string,
  className: PropTypes.string,
  linkClassName: PropTypes.string,
  showBadge: PropTypes.bool,
};

export default UserDisplay;
