import { RoutePoint } from '../model/CaptureModels';

export class RoutePlanner {
  static serpentine(width: number, height: number, spacing: number, originX: number, originY: number,
    includeCrossScan: boolean = false): RoutePoint[] {
    let route: RoutePoint[] = [];
    let safeSpacing = Math.max(0.2, Math.min(spacing, 1.0));
    let rowCount = Math.max(1, Math.ceil(height / safeSpacing));
    for (let row = 0; row <= rowCount; row++) {
      let y = originY + Math.min(row * safeSpacing, height);
      if (row % 2 === 0) {
        route.push(new RoutePoint(originX, y, 'horizontal'));
        route.push(new RoutePoint(originX + width, y, 'horizontal'));
      } else {
        route.push(new RoutePoint(originX + width, y, 'horizontal'));
        route.push(new RoutePoint(originX, y, 'horizontal'));
      }
    }

    if (!includeCrossScan) {
      return RoutePlanner.removeConsecutiveDuplicates(route);
    }

    // Continue the vertical pass from the corner where the horizontal pass ended.
    // This avoids returning to the origin and then retracing the same boundary line.
    let columnCount = Math.max(1, Math.ceil(width / safeSpacing));
    let lastPoint = route[route.length - 1];
    let startFromRight = Math.abs(lastPoint.x - (originX + width)) < 0.0001;
    for (let step = 0; step <= columnCount; step++) {
      let column = startFromRight ? columnCount - step : step;
      let x = originX + Math.min(column * safeSpacing, width);
      if (step % 2 === 0) {
        route.push(new RoutePoint(x, originY + height, 'vertical'));
        route.push(new RoutePoint(x, originY, 'vertical'));
      } else {
        route.push(new RoutePoint(x, originY, 'vertical'));
        route.push(new RoutePoint(x, originY + height, 'vertical'));
      }
    }
    return RoutePlanner.removeConsecutiveDuplicates(route);
  }

  static reversed(points: RoutePoint[]): RoutePoint[] {
    let result: RoutePoint[] = [];
    for (let index = points.length - 1; index >= 0; index--) {
      let point = points[index];
      result.push(new RoutePoint(point.x, point.y, point.phase));
    }
    return result;
  }

  static initialHeadingDegrees(points: RoutePoint[], fallback: number = 0): number {
    if (points.length < 2) {
      return fallback;
    }
    let first = points[0];
    for (let index = 1; index < points.length; index++) {
      let dx = points[index].x - first.x;
      let dy = points[index].y - first.y;
      if (Math.sqrt(dx * dx + dy * dy) > 0.0001) {
        let degrees = Math.atan2(dy, dx) * 180 / Math.PI;
        return (degrees % 360 + 360) % 360;
      }
    }
    return fallback;
  }

  private static removeConsecutiveDuplicates(points: RoutePoint[]): RoutePoint[] {
    let result: RoutePoint[] = [];
    for (let index = 0; index < points.length; index++) {
      let point = points[index];
      let previous = result.length > 0 ? result[result.length - 1] : undefined;
      if (!previous || Math.abs(previous.x - point.x) > 0.0001 || Math.abs(previous.y - point.y) > 0.0001) {
        result.push(point);
      }
    }
    return result;
  }
}
