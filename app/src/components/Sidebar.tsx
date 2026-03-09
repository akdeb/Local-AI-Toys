import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Users, LockKeyhole, Volume2, Settings, History, Plus, Home, Dot } from 'lucide-react';
import clsx from 'clsx';
import { useActiveUser } from '../state/ActiveUserContext';
import { useEffect, useState } from 'react';
import { api } from '../api';
import { Logo } from './Logo';
import elatoPng from '../assets/elato.png';
import { Modal } from './Modal';
import { CreateTiles } from './CreateTiles';

const ICON_SIZE = 28

const NavItem = ({
  to,
  icon: Icon,
  label,
  trailingIcon: TrailingIcon,
  matchPath,
  iconOnly = false,
  className = "",
}: {
  to: string;
  icon: any;
  label: string;
  trailingIcon?: any;
  matchPath?: string;
  iconOnly?: boolean;
  className?: string;
}) => {
  const location = useLocation();
  const isActive = matchPath ? location.pathname === matchPath : location.pathname === to;

  return (
    <Link
      to={to}
      className={clsx(
        "sidebar-nav-item flex items-center transition-colors",
        iconOnly ? "justify-center w-full h-10 rounded-2xl" : "gap-3 px-4 py-3",
        isActive
          ? "bg-gray-100 text-black"
          : "bg-white text-gray-900 hover:bg-gray-50",
        className
      )}
      aria-label={label}
    >
      <Icon size={20} />
      {iconOnly ? (
        <span className="sr-only">{label}</span>
      ) : (
        <span className={`${isActive ? "font-bold" : "font-medium"} flex-1`}>{label}</span>
      )}
      {!iconOnly && TrailingIcon && <TrailingIcon size={16} className="opacity-30 shrink-0" />}
    </Link>
  );
};

export const Sidebar = () => {
  const navigate = useNavigate();
  const { activeUser } = useActiveUser();
  const [_activePersonalityName, setActivePersonalityName] = useState<string | null>(null);
  const [activeExperienceId, setActiveExperienceId] = useState<string | null>(null);
  const [_activeExperienceType, setActiveExperienceType] = useState<string | null>(null);
  const [_deviceConnected, setDeviceConnected] = useState<boolean>(false);
  const [_deviceSessionId, setDeviceSessionId] = useState<string | null>(null);
  const [createMenuOpen, setCreateMenuOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const ds = { connected: false, session_id: null };
        // const ds = await api.getDeviceStatus().catch(() => ({ connected: false, session_id: null }));
        if (!cancelled) {
          setDeviceConnected(!!ds?.connected);
          setDeviceSessionId(ds?.session_id || null);
        }

        const selectedId = activeUser?.current_personality_id;
        if (!selectedId) {
          if (!cancelled) setActivePersonalityName(null);
          if (!cancelled) {
            setActiveExperienceId(null);
            setActiveExperienceType(null);
          }
          return;
        }

        const ps = await api.getPersonalities(true).catch(() => []);
        const selected = ps.find((p: any) => p.id === selectedId);
        if (!cancelled) {
          setActivePersonalityName(selected?.name || null);
          setActiveExperienceId(selected?.id ? String(selected.id) : null);
          setActiveExperienceType(selected?.type ? String(selected.type) : null);
        }
      } catch {
        // ignore
      }
    };

    load();
  }, [activeUser?.current_personality_id]);

  return (
    <div className="sidebar-wrap w-64 shrink-0 bg-transparent p-6 flex flex-col gap-6 h-full overflow-y-auto overscroll-contain justify-between">
      <div className="sidebar-shell bg-white rounded-[24px] overflow-hidden shadow-[0_12px_28px_rgba(0,0,0,0.08)] border border-gray-200">
        <div className="sidebar-brand p-4 pb-2 bg-white text-black flex flex-col items-center">
          <Logo />
          <p className="text-xs font-mono opacity-90">Where Toys Come Alive</p>
        </div>
        <div className="bg-transparent border-gray-200">
          <nav className="flex flex-col">
            <div className="p-4 pb-6">
              <button
                type="button"
                className="retro-btn w-full flex items-center justify-center gap-2"
                onClick={() => setCreateMenuOpen(true)}
              >
                <Plus size={16} />
                Create
              </button>
            </div>
            <NavItem
              to={
                activeExperienceId
                  ? `/?focus=${encodeURIComponent(activeExperienceId)}`
                  : "/"
              }
              icon={Home}
              label="Home"
              matchPath="/"
            />
            <NavItem to="/voices" icon={Volume2} label="Voices" />
            <div className="grid grid-cols-3 gap-2 px-3 pb-3 w-full mt-3">
              <NavItem
                to="/conversations"
                icon={History}
                label="Sessions"
                trailingIcon={LockKeyhole}
                iconOnly
              />
              <NavItem to="/users" icon={Users} label="Members" iconOnly />
              <NavItem to="/settings" icon={Settings} label="Settings" iconOnly />
            </div>
          </nav>
        </div>
      </div>
      <div className="sidebar-footer flex flex-col gap-3 flex-wrap text-xs font-mono">
        <a
          href="https://www.elatoai.com/products"
          target="_blank"
          rel="noreferrer"
          className="inline-flex w-fit opacity-70 hover:opacity-100 hover:scale-105 transition-all hover:-rotate-3 duration-300 ease-in-out"
        >
          <img src={elatoPng} alt="Elato" className="w-18 h-auto object-contain" />
        </a>
        <div className="flex items-center" style={{ fontSize: '10px'}}>
        <a
          href="https://www.elatoai.com/products"
          target="_blank"
          rel="noreferrer"
          className="underline underline-offset-4 opacity-70 hover:opacity-100"
        >
          DIY AI Toys
        </a>
        <Dot size={16} className="opacity-70" />
          <a
            href="mailto:akash@elatoai.com"
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-4 opacity-70 hover:opacity-100"
          >
            Get Support
          </a>
</div>

      </div>
      <Modal
        open={createMenuOpen}
        icon={<Plus size={24} />}
        title="Create"
        onClose={() => setCreateMenuOpen(false)}
        panelClassName="w-full max-w-3xl"
      >
        <CreateTiles
          iconSize={ICON_SIZE}
          onSelect={(kind) => {
            if (kind === "voice") {
              setCreateMenuOpen(false);
              navigate("/voices?create=voice");
              return;
            }
            const tab = kind === "character" ? "personality" : kind;
            setCreateMenuOpen(false);
            navigate(`/?tab=${tab}&create=1`);
          }}
        />
      </Modal>
    </div>
  );
};
