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
  const cardStyle = {
    width: '280px',
    height: '300px',
    objectFit: 'cover',
    backgroundColor: card.isActive ? 'honeydew' : '#fff',
  };
  const cardBackStyle = {
    width: '280px',
    height: '300px',
    objectFit: 'cover',
    backgroundColor: 'white',
  };

  return (
    <ReactCardFlip isFlipped={isFlipped} flipDirection="horizontal">
      <div className="flip-card-front" onClick={handleFlip} style={cardStyle}>
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
        className="flip-card-back"
        onClick={handleFlip}
        style={cardBackStyle}
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
