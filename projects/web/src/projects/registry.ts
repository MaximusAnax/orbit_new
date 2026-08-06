/** Which projects have screens. A project absent here still works via its API,
 *  and the shell says so plainly rather than showing a dead link. */
import type { ComponentType } from "react";
import Ethos from "./ethos";
import Almanac from "./almanac";
import Flowlist from "./flowlist";
import ChessMentor from "./chessmentor";
import DressCast from "./dresscast";
import PointsMax from "./pointsmax";
import NewsAlpha from "./newsalpha";
import TickerPress from "./tickerpress";
import GrailTrader from "./grailtrader";
import DataSweep from "./datasweep";
import FormCoach from "./formcoach";
import VoiceKin from "./voicekin";

export const SCREENS: Record<string, ComponentType> = {
  ethos: Ethos,
  almanac: Almanac,
  flowlist: Flowlist,
  chessmentor: ChessMentor,
  dresscast: DressCast,
  pointsmax: PointsMax,
  newsalpha: NewsAlpha,
  tickerpress: TickerPress,
  grailtrader: GrailTrader,
  datasweep: DataSweep,
  formcoach: FormCoach,
  voicekin: VoiceKin,
};
