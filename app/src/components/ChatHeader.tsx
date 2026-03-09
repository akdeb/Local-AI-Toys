import { ChatSplitAvatar } from "./ChatSplitAvatar";

type ChatHeaderProps = {
  userName: string;
  characterName: string;
  userEmoji?: string | null;
  characterImageSrc?: string | null;
  onCharacterClick?: () => void;
  className?: string;
};

export const ChatHeader = ({
  userName,
  characterName,
  userEmoji,
  characterImageSrc,
  onCharacterClick,
  className = "",
}: ChatHeaderProps) => {
  return (
    <div className={`bg-transparent border-0 shadow-none rounded-none px-4 py-3 ${className}`}>
      <div className="flex items-center gap-3">
        <ChatSplitAvatar
          size={48}
          userEmoji={userEmoji}
          characterImageSrc={characterImageSrc}
          onCharacterClick={onCharacterClick}
        />

        <div>
          <div className="text-xs font-bold uppercase tracking-wider">Chat</div>
          <div className="font-mono text-sm text-gray-900 wrap-break-word">
            {userName} {"<>"} {characterName}
          </div>
        </div>
      </div>
    </div>
  );
};
