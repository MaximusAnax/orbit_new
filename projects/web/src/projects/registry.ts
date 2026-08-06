/** Which projects have screens. A project absent here still works via its API,
 *  and the shell says so plainly rather than showing a dead link. */
import type { ComponentType } from "react";
import Ethos from "./ethos";
import DataSweep from "./datasweep";

export const SCREENS: Record<string, ComponentType> = {
  ethos: Ethos,
  datasweep: DataSweep,
};
