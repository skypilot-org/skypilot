import React, { useState } from 'react';
import ReactCardFlip from 'react-card-flip';
import Link from 'next/link';
import Image from 'next/image';
import { Button } from '@/components/ui/button';

export const FlipCard = ({ card }) => {
  const [isFlipped, setIsFlipped] = useState(false);

  const handleFlip = (e) => {
    e.preventDefault();
    setIsFlipped(!isFlipped);
  };
  const cardDimensions = {
    width: '280px',
    height: '300px',
    objectFit: 'cover',
  };

  return (
    <ReactCardFlip isFlipped={isFlipped} flipDirection="horizontal">
      <div
        className={`flip-card-front ${card.isActive ? 'bg-green-50 dark:bg-green-900/30' : 'bg-white dark:bg-gray-900'}`}
        onClick={handleFlip}
        style={cardDimensions}
      >
        <div style={{ width: '80%', height: '300px', position: 'relative' }}>
          <Image
            src={card.image}
            alt={card.title}
            fill
            style={{ objectFit: 'contain' }}
          />
        </div>
        {/* <h3>{card.title}</h3> */}
      </div>

      <div
        className="flip-card-back bg-white dark:bg-gray-900"
        onClick={handleFlip}
        style={cardDimensions}
      >
        <p>{card.description}</p>
        <Link href={card.detailsLink}>
          <Button variant="outline">
            {card.isActive ? 'More Details' : 'Connect'}
          </Button>
        </Link>
      </div>
    </ReactCardFlip>
  );
};
